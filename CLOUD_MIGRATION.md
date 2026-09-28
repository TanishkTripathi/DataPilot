# DataPilot v3 – Cloud Migration Foundation

This version keeps DataPilot usable locally while preparing the codebase for Google Cloud deployment.

## Already prepared

- Production-style Uvicorn startup on `0.0.0.0` using `$PORT`
- Dockerfile and `.dockerignore`
- Relative frontend API routing (`/api`)
- Configurable CORS through `ALLOWED_ORIGINS`
- Configurable upload-size validation through `MAX_UPLOAD_SIZE_MB`
- Central binary-storage abstraction in `backend/services/blob_storage.py`
- Local storage remains the default for development
- Optional Google Cloud Storage backend is available with `STORAGE_BACKEND=gcs`
- Real `.env` is intentionally excluded; use `.env.example`
- Local Chroma and object data are excluded from Docker build context

## Not yet enabled

The following still require actual cloud resources and credentials:

1. Firestore for persistent file/report/conversation metadata
2. Chroma Cloud for persistent vector storage
3. Firebase Authentication for user identity
4. Secret Manager for production secrets
5. Cloud Run deployment and Artifact Registry

The application still keeps its active `FILES`/`REPORTS` objects in process memory. That is intentional for this migration step and should be replaced with Firestore-backed state before production multi-instance use.

## Local run

```bash
source .venv/bin/activate
python run.py
```

## Docker local test

```bash
docker build -t datapilot:v3 .
docker run --rm -p 8000:8080 --env-file .env datapilot:v3
```

## GCS switch

After creating a bucket and configuring Google authentication:

```env
STORAGE_BACKEND=gcs
GCS_BUCKET_NAME=your-bucket-name
```

Do not commit credentials or real API keys.
