FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY apps/__init__.py ./apps/
COPY apps/orchestrator/__init__.py ./apps/orchestrator/__init__.py
COPY apps/orchestrator/models ./apps/orchestrator/models
COPY apps/orchestrator/integrations ./apps/orchestrator/integrations
COPY apps/orchestrator/meetings/__init__.py apps/orchestrator/meetings/models.py apps/orchestrator/meetings/evidence.py ./apps/orchestrator/meetings/
COPY agents/meetings ./agents/meetings
USER 1000:1000
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "agents.meetings.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers"]
