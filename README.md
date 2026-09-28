# DataPilot v3 – Cloud-Ready Foundation

This package is the cleaned and fixed v3 foundation for DataPilot. It remains runnable locally while preparing the application for Google Cloud deployment.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Put your Gemini API key in .env
python run.py
```

Open `http://127.0.0.1:8000`.

## Docker

```bash
docker build -t datapilot:v3 .
docker run --rm -p 8000:8080 --env-file .env datapilot:v3
```

## Current cloud-ready changes

- FastAPI production entrypoint listens on `0.0.0.0` and `$PORT` inside Docker.
- Frontend uses relative `/api` requests.
- CORS is configurable with `ALLOWED_ORIGINS`.
- File extension and upload-size validation are enabled.
- Binary file storage is abstracted in `backend/services/blob_storage.py`.
- Local object storage is the default; Google Cloud Storage can be selected with `STORAGE_BACKEND=gcs`.
- Local ChromaDB remains available for development.
- Firestore, Firebase Authentication, Chroma Cloud, Secret Manager, and final Cloud Run deployment are intentionally not enabled until their cloud resources are configured.

## Important

Do not commit `.env`, API keys, `.venv`, Chroma data, or local object storage.
