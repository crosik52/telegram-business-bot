FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1     MPLCONFIGDIR=/tmp/matplotlib OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg     && rm -rf /var/lib/apt/lists/*     && groupadd --gid 10001 savelog && useradd --uid 10001 --gid 10001 --no-create-home savelog
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# Explicit allowlist: no .env, git history, screenshots, logs or local databases.
COPY app ./app
COPY deploy/vps/preflight.py ./deploy/vps/preflight.py
# Migrations are packaged for an explicit operator command, never run at startup.
COPY alembic-clean.ini ./alembic-clean.ini
COPY alembic_clean ./alembic_clean
USER 10001:10001
EXPOSE 8000
CMD ["sh", "-c", "python deploy/vps/preflight.py && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --no-access-log --timeout-graceful-shutdown 45"]
