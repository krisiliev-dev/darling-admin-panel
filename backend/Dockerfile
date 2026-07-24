# Дарлинг admin panel — backend runtime image.
#
# Deps ONLY: the application code is bind-mounted at runtime (see admin-panel/docker-compose.yml),
# so agents iterate without rebuilding the image and the SQLite DBs persist on the host. Rebuild
# this image only when requirements.txt changes (`docker compose build panel`).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app/admin-panel/backend

# Only requirements.txt is in the build context (see .dockerignore) — keeps the layer cache stable.
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# The code + pipeline.db + admin.db arrive via the bind mount at runtime; this WORKDIR is overlaid.
EXPOSE 8770
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8770", "--no-proxy-headers"]
