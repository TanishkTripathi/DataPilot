FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Build tools for native Python dependencies such as numpy/torch when wheels
# are not available for the selected platform.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend
COPY run.py ./run.py
COPY README.md ./README.md

RUN mkdir -p /app/storage/chroma /app/storage/objects

ENV PORT=10080
EXPOSE 10080

# Cloud Run supplies PORT; 0.0.0.0 is required inside the container.
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}"]
