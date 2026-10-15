FROM python:3.11-slim

# Redis runs inside this same container (Q6 needs a real Redis)
RUN apt-get update && \
    apt-get install -y --no-install-recommends redis-server && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, code second: editing main.py will NOT reinstall packages
# (this is exactly the layer-cache idea behind Q14)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

# Start Redis in the background, then FastAPI on Render's $PORT (default 10000)
CMD ["sh", "-c", "redis-server --daemonize yes --save '' --appendonly no && exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"]
