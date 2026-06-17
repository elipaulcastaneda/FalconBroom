FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first so layer caches can be reused
COPY requirements.txt /app/requirements.txt

RUN python -m pip install --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt

# Copy the rest of the app
COPY . /app

EXPOSE ${PORT}

# Run uvicorn using the PORT env var (Render provides $PORT at runtime)
CMD ["sh", "-c", "uvicorn fbroom.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
