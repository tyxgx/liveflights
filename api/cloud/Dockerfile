# Lambda container image for the cloud API (api/cloud/app.py, Mangum-wrapped).
# Built from the repo root: docker build -f api/cloud/Dockerfile .
FROM public.ecr.aws/lambda/python:3.12

COPY api/cloud/requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY api/cloud/app.py ${LAMBDA_TASK_ROOT}/app.py

# Vendored: app.py's airline-activity stat needs the same static
# callsign-prefix lookup the (paused) transform Lambda used to use for
# gold.airline_activity — reused as-is rather than duplicated.
COPY streaming/utils/airlines.py ${LAMBDA_TASK_ROOT}/utils/airlines.py
RUN touch ${LAMBDA_TASK_ROOT}/utils/__init__.py

CMD ["app.handler"]
