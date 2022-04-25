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


usage () {
  echo "usage: mltrain.sh [local | train | tune] <input_file>.csv [--use-optimized]

Use 'local' to train locally with a local data file, and 'train' and 'tune' to
run on ML Engine.  For ML Engine jobs the input file must reside on GCS.

Optional args:
  --use-optimized:  Use optimized hyperparamters, default False.

Examples:

# train locally with unoptimized hyperparams
./mltrain.sh local ../data/token_balances.csv

# train on ML Engine with optimized hyperparams
./mltrain.sh train gs://your_bucket/data/token_balances.csv --use-optimized

# tune hyperparams on ML Engine:
./mltrain.sh tune gs://your_bucket/data/token_balances.csv
"

}

date

TIME=`date +"%Y%m%d_%H%M%S"`

# CHANGE TO YOUR BUCKET
BUCKET="gs://your_bucket"


# set job vars
TRAIN_JOB="$1"
TRAIN_FILE="$2"
JOB_NAME=wals_ml_${TRAIN_JOB}_${TIME}
REGION=us-central1

# add additional args
shift; shift
ARGS="--train-files ${TRAIN_FILE} --verbose-logging $@"

if [[ ${TRAIN_JOB} == "local" ]]; then

  mkdir -p jobs/${JOB_NAME}

  python3 ./trainer/task.py \
    --job-dir ./jobs/${JOB_NAME} \
    ${ARGS}

elif [[ ${TRAIN_JOB} == "train" ]]; then

  gcloud ml-engine jobs submit training ${JOB_NAME} \
    --region $REGION \
    --scale-tier=CUSTOM \
    --job-dir ${BUCKET}/jobs/${JOB_NAME} \
    --module-name trainer.task \
    --package-path trainer \
    --config trainer/config/config_train.json \
    -- \
    ${ARGS}

elif [[ $TRAIN_JOB == "tune" ]]; then

  # set configuration for tuning
  CONFIG_TUNE="trainer/config/config_tune.json"

  gcloud ml-engine jobs submit training ${JOB_NAME} \
    --region ${REGION} \
    --scale-tier=CUSTOM \
    --job-dir ${BUCKET}/jobs/${JOB_NAME} \
    --module-name trainer.task \
    --package-path trainer \
    --config ${CONFIG_TUNE} \
    -- \
    --hypertune \
    ${ARGS}

else
  usage
fi

date
