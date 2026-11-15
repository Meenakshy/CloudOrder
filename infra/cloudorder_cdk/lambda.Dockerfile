FROM public.ecr.aws/lambda/python:3.12

COPY infra/cloudorder_cdk/runtime-requirements.txt ${LAMBDA_TASK_ROOT}/runtime-requirements.txt
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/runtime-requirements.txt

COPY cloudorder ${LAMBDA_TASK_ROOT}/cloudorder
COPY workers ${LAMBDA_TASK_ROOT}/workers

CMD ["workers.lambda_handlers.starter_handler"]