FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app KAI_BINDING_MODE=managed
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
# Audit file directory for the audit-logs volume (P6-11); a fresh named volume inherits this owner.
RUN mkdir -p /var/log/kai && chown 10001:10001 /var/log/kai
USER 10001:10001
CMD ["python", "-m", "mcp_clickhouse.main"]
