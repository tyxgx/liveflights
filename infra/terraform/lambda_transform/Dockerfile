# Lambda container image for the bronze -> silver -> gold transform.
# Built from infra/terraform: docker build -f lambda_transform/Dockerfile .
FROM public.ecr.aws/lambda/python:3.12

COPY lambda_transform/requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY lambda_transform/handler.py ${LAMBDA_TASK_ROOT}/handler.py

CMD ["handler.handler"]
