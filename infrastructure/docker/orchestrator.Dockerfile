FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Official PGDG packages; match Spark's PG17 major for whole-database backups.
ARG POSTGRES_CLIENT_VERSION=17.11-1.pgdg12+2
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl --fail --silent --show-error https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends postgresql-client-17=$POSTGRES_CLIENT_VERSION \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY apps/__init__.py apps/auth_logging.py ./apps/
COPY apps/orchestrator ./apps/orchestrator
COPY tooling/migrate_research.py ./tooling/migrate_research.py
COPY infrastructure/deployment/scripts/postgres_admin.py /opt/toir/postgres_admin.py
USER 1000:1000
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "apps.orchestrator.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers"]
