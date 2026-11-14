FROM python:3.12.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/workspace:/workspace/data-ingestion
COPY data-ingestion/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock && pip check
COPY shared /workspace/shared
COPY data-ingestion /workspace/data-ingestion
WORKDIR /workspace/data-ingestion
CMD ["python", "-m", "app.main"]
