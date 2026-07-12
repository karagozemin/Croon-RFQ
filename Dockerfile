# CROON RFQ — backend container (DigitalOcean App Platform / any Docker host)
FROM python:3.11-slim

WORKDIR /app

# Install deps first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Where the SQLite file lives when no external DB is configured. This is a
# dedicated directory (NOT /tmp) so a host volume can be mounted here to make
# settlement history survive redeploys. On DigitalOcean App Platform, either:
#   (a) attach a Volume mounted at /data (keeps this SQLite file), OR
#   (b) provision a Managed Postgres and override CROON_DATABASE_URL with its
#       connection string (recommended — fully durable, no volume needed).
# db.py auto-creates this directory and picks the right engine per dialect.
RUN mkdir -p /data
VOLUME ["/data"]

# App Platform injects PORT (default 8080); bind on all interfaces.
# Default: live-first with automatic mock failover (FailoverCapClient).
# Without a CROON_CROO_SDK_KEY env var the app boots straight into mock.
ENV CROON_HOST=0.0.0.0 \
    CROON_PORT=8080 \
    CROON_CAP_MODE=live \
    CROON_DATABASE_URL=sqlite:////data/croon.db

EXPOSE 8080


CMD ["sh", "-c", "uvicorn croon.api:app --host 0.0.0.0 --port ${PORT:-8080}"]
