# Lambda container image for the trajectory-prediction Lambda (predict/handler.py).
# Built from the repo root: docker build -f predict/Dockerfile .
#
# A container image, not the ingest Lambda's plain zip, because this one needs numpy +
# onnxruntime (no pure-stdlib way to run an ONNX model) — same reasoning as api/cloud/Dockerfile,
# which is already a container image for its numpy/scikit-learn dependency.
FROM public.ecr.aws/lambda/python:3.12

COPY predict/requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY predict/handler.py ${LAMBDA_TASK_ROOT}/handler.py

# The shared feature module (ml/features.py) — the SAME code the training pipeline uses to build
# X/S, imported here unmodified so live serving can never silently drift from what the model was
# trained on (see ml/features.py's own module docstring on FEATURES_VERSION / train-serve skew).
COPY ml/features.py ${LAMBDA_TASK_ROOT}/features.py

# callsign -> {origin, destination} scheduled-route lookup, for the dashboard's "from X to Y" label
# on click (display only, does not feed the model — see route_lookup.py's own docstring). VRS
# standing data is CC0 and small enough (~22 MB) to bundle straight into the image instead of an
# S3 round trip on every cold start.
COPY ml/route_lookup.py ${LAMBDA_TASK_ROOT}/route_lookup.py
COPY data/vrs/routes.csv ${LAMBDA_TASK_ROOT}/data/vrs/routes.csv
COPY data/vrs/airports.csv ${LAMBDA_TASK_ROOT}/data/vrs/airports.csv

CMD ["handler.handler"]
