# Copyright 2017 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""WALS model input data, training and predict functions."""

import datetime
import numpy as np
import os
import pandas as pd
from scipy.sparse import coo_matrix
import sh
import tensorflow as tf

import wals

# ratio of train set size to test set size
TEST_SET_RATIO = 10

# default hyperparameters
DEFAULT_PARAMS = {
    'weights': True,
    'latent_factors': 5,
    'num_iters': 20,
    'regularization': 0.07,
    'unobs_weight': 0.01,
    'wt_type': 0,
    'feature_wt_factor': 130.0,
    'feature_wt_exp': 0.08
}

# parameters optimized with hypertuning
OPTIMIZED_PARAMS = {
    'latent_factors': 22,
    'regularization': 0.12,
    'unobs_weight': 0.001,
    'feature_wt_exp': 9.43,
}


def create_test_and_train_sets(input_file):
  """Create test and train sets.

  Args:
    input_file: path to csv data file

  Returns:
    array of user addresses for each row of the ratings matrix
    array of token addresses for each column of the rating matrix
    sparse coo_matrix for training
    sparse coo_matrix for test

  Raises:
    ValueError: if invalid data type is supplied
  """

  return _token_balances_train_and_test(input_file)


def _token_balances_train_and_test(input_file):
  """Load token_balances dataset, and create train and set sparse matrices.

  Assumes 'token_address', 'user_address', and 'rating' columns.

  Args:
    input_file: path to csv data file

  Returns:
    array of user addresses for each row of the ratings matrix
    array of token addresses for each column of the rating matrix
    sparse coo_matrix for training
    sparse coo_matrix for test
  """
  headers = ['token_address', 'user_address', 'rating']
  balances_df = pd.read_csv(input_file,
                           sep=',',
                           names=headers,
                           header=0,
                           dtype={
                               'token_address': np.str,
                               'user_address': np.str,
                               'rating': np.float32,
                           })

  df_tokens = pd.DataFrame({'token_address': balances_df.token_address.unique()})
  df_sorted_tokens = df_tokens.sort_values('token_address').reset_index()
  pds_tokens = df_sorted_tokens.token_address

  # preprocess data. df.groupby.agg sorts user_address and token_address
  df_user_token_addresses = balances_df.groupby(['user_address', 'token_address']).agg({'rating': 'sum'})

  # create a list of (user_address, token_address, rating) records, where user_address and
  # token_address are 0-indexed
  current_user = -1
  user_index = -1
  ratings = []
  users = []
  for user_token_rating in df_user_token_addresses.itertuples():
    user = user_token_rating[0][0]
    token = user_token_rating[0][1]

    # as we go, build a (sorted) list of user addresses
    if user != current_user:
      users.append(user)
      user_index += 1
      current_user = user

    # this search makes the preprocessing time O(r * i log(i)),
    # r = # ratings, i = # tokens
    token_index = pds_tokens.searchsorted(token)
    ratings.append((user_index, token_index, user_token_rating[1]))

  # convert ratings list and user list to np array
  ratings = np.asarray(ratings)
  users = np.asarray(users)

  # create train and test sets
  train_sparse, test_sparse = _create_sparse_train_and_test(ratings,
                                                         user_index + 1,
                                                         df_tokens.size)

  return users, pds_tokens.to_numpy(), train_sparse, test_sparse

def _create_sparse_train_and_test(ratings, n_users, n_items):
  """Given ratings, create sparse matrices for train and test sets.

  Args:
    ratings:  list of ratings tuples  (u, i, r)
    n_users:  number of users
    n_items:  number of items

  Returns:
     train, test sparse matrices in scipy coo_matrix format.
  """
  # pick a random test set of entries, sorted ascending
  test_set_size = len(ratings) // TEST_SET_RATIO
  test_set_idx = np.random.choice(list(range(len(ratings))),
                                  size=test_set_size, replace=False)
  test_set_idx = sorted(test_set_idx)

  # sift ratings into train and test sets
  ts_ratings = ratings[test_set_idx]
  tr_ratings = np.delete(ratings, test_set_idx, axis=0)

  # create training and test matrices as coo_matrix's
  u_tr, i_tr, r_tr = zip(*tr_ratings)
  tr_sparse = coo_matrix((r_tr, (u_tr, i_tr)), shape=(n_users, n_items))

  u_ts, i_ts, r_ts = zip(*ts_ratings)
  test_sparse = coo_matrix((r_ts, (u_ts, i_ts)), shape=(n_users, n_items))

  return tr_sparse, test_sparse


def train_model(args, tr_sparse):
  """Instantiate WALS model and use "simple_train" to factorize the matrix.

  Args:
    args: training args containing hyperparams
    tr_sparse: sparse training matrix

  Returns:
     the row and column factors in numpy format.
  """
  dim = args['latent_factors']
  num_iters = args['num_iters']
  reg = args['regularization']
  unobs = args['unobs_weight']
  wt_type = args['wt_type']
  feature_wt_exp = args['feature_wt_exp']
  obs_wt = args['feature_wt_factor']

  tf.logging.info('Train Start: {:%Y-%m-%d %H:%M:%S}'.format(datetime.datetime.now()))

  # generate model
  input_tensor, row_factor, col_factor, model = wals.wals_model(tr_sparse,
                                                                dim,
                                                                reg,
                                                                unobs,
                                                                args['weights'],
                                                                wt_type,
                                                                feature_wt_exp,
                                                                obs_wt)

  # factorize matrix
  session = wals.simple_train(model, input_tensor, num_iters)

  tf.logging.info('Train Finish: {:%Y-%m-%d %H:%M:%S}'.format(datetime.datetime.now()))

  # evaluate output factor matrices
  output_row = row_factor.eval(session=session)
  output_col = col_factor.eval(session=session)

  # close the training session now that we've evaluated the output
  session.close()

  return output_row, output_col


def save_model(args, user_map, item_map, row_factor, col_factor):
  """Save the user map, item map, row factor and column factor matrices in numpy format.

  These matrices together constitute the "recommendation model."

  Args:
    args:         input args to training job
    user_map:     user map numpy array
    item_map:     item map numpy array
    row_factor:   row_factor numpy array
    col_factor:   col_factor numpy array
  """
  model_dir = os.path.join(args['output_dir'], 'model')

  # if our output directory is a GCS bucket, write model files to /tmp,
  # then copy to GCS
  gs_model_dir = None
  if model_dir.startswith('gs://'):
    gs_model_dir = model_dir
    model_dir = '/tmp/{0}'.format(args['job_name'])

  os.makedirs(model_dir)
  np.save(os.path.join(model_dir, 'user'), user_map)
  np.save(os.path.join(model_dir, 'item'), item_map)
  np.save(os.path.join(model_dir, 'row'), row_factor)
  np.save(os.path.join(model_dir, 'col'), col_factor)

  if gs_model_dir:
    sh.gsutil('cp', '-r', os.path.join(model_dir, '*'), gs_model_dir)


def generate_recommendations(user_idx, user_rated, row_factor, col_factor, k):
  """Generate recommendations for a user.

  Args:
    user_idx: the row index of the user in the ratings matrix,

    user_rated: the list of item indexes (column indexes in the ratings matrix)
      previously rated by that user (which will be excluded from the
      recommendations)

    row_factor: the row factors of the recommendation model
    col_factor: the column factors of the recommendation model

    k: number of recommendations requested

  Returns:
    list of k item indexes with the predicted highest rating, excluding
    those that the user has already rated
  """

  # bounds checking for args
  assert (row_factor.shape[0] - len(user_rated)) >= k

  # retrieve user factor
  user_f = row_factor[user_idx]

  # dot product of item factors with user factor gives predicted ratings
  pred_ratings = col_factor.dot(user_f)

  # find candidate recommended item indexes sorted by predicted rating
  k_r = k + len(user_rated)
  candidate_items = np.argsort(pred_ratings)[-k_r:]

  # remove previously rated items and take top k
  recommended_items = [i for i in candidate_items if i not in user_rated]
  recommended_items = recommended_items[-k:]

  # flip to sort highest rated first
  recommended_items.reverse()

  return recommended_items

