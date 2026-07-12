# CROON RFQ — backend container (DigitalOcean App Platform / any Docker host)
FROM python:3.11-slim

WORKDIR /app

# Install deps first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# App Platform injects PORT (default 8080); bind on all interfaces.
ENV CROON_HOST=0.0.0.0 \
    CROON_PORT=8080 \
    CROON_CAP_MODE=mock \
    CROON_DATABASE_URL=sqlite:////tmp/croon.db

EXPOSE 8080

CMD ["sh", "-c", "uvicorn croon.api:app --host 0.0.0.0 --port ${PORT:-8080}"]
