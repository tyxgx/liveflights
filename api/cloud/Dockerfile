# Lambda container image for the cloud API (api/cloud/app.py, Mangum-wrapped).
# Built from the repo root: docker build -f api/cloud/Dockerfile .
FROM public.ecr.aws/lambda/python:3.12

COPY api/cloud/requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY api/cloud/app.py ${LAMBDA_TASK_ROOT}/app.py

CMD ["app.handler"]
