import io
import os
import re
import uuid
import json
import sqlite3
import tempfile
import shutil
from pathlib import Path
from typing import Dict, Any, List

import numpy as np
import pandas as pd
import pymupdf

from docx import Document
from dotenv import load_dotenv

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from pydantic import BaseModel

from backend.services.blob_storage import put_bytes, delete_object

import chromadb
from sentence_transformers import SentenceTransformer

from google import genai
from google.genai import types

from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import (
    getSampleStyleSheet,
    ParagraphStyle,
)
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
)


# ============================================================
# CONFIGURATION
# ============================================================

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

LLM_MODEL = os.getenv(
    "LLM_MODEL",
    "gemini-3.5-flash-lite"
)

EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "BAAI/bge-small-en-v1.5"
)

# Chroma Cloud is used for vector storage.
# Connection is configured through:
# CHROMA_API_KEY
# CHROMA_TENANT
# CHROMA_DATABASE


MAX_REPORT_CONTEXT_CHARS = int(
    os.getenv(
        "MAX_REPORT_CONTEXT_CHARS",
        "50000"
    )
)

MAX_UPLOAD_SIZE_BYTES = int(
    os.getenv("MAX_UPLOAD_SIZE_MB", "50")
) * 1024 * 1024

ALLOWED_EXTENSIONS = {
    ".pdf", ".docx", ".txt", ".md",
    ".csv", ".xlsx", ".xls"
}


# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(
    title="DataPilot API",
    version="3.0.0"
)


ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "http://127.0.0.1:8000,http://localhost:8000"
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# FRONTEND PATH
# ============================================================

@app.get("/")
def frontend():
    return FileResponse(
        FRONTEND_DIR / "index.html"
    )


@app.get("/style.css")
def frontend_css():
    return FileResponse(
        FRONTEND_DIR / "style.css"
    )


@app.get("/app.js")
def frontend_js():
    return FileResponse(
        FRONTEND_DIR / "app.js"
    )


# ============================================================
# APPLICATION STATE
# ============================================================

# Local prototype storage.
#
# Each uploaded file receives a unique file_id.
#
# Example:
#
# FILES = {
#     "uuid-1": {
#         "filename": "report.pdf",
#         "kind": "text",
#         ...
#     },
#
#     "uuid-2": {
#         "filename": "sales.xlsx",
#         "kind": "dataset",
#         ...
#     }
# }

FILES: Dict[str, Dict[str, Any]] = {}

REPORTS: Dict[str, Dict[str, Any]] = {}


# Cached resources
_embedding_model = None
_chroma = None
_gemini = None


# ============================================================
# RESOURCE HELPERS
# ============================================================

def embedding_model():

    global _embedding_model

    if _embedding_model is None:

        import torch

        device = (
            "mps"
            if torch.backends.mps.is_available()
            else "cpu"
        )

        _embedding_model = SentenceTransformer(
            EMBEDDING_MODEL,
            device=device
        )

    return _embedding_model


def chroma_client():

    global _chroma

    if _chroma is None:

        api_key = os.getenv("CHROMA_API_KEY")
        tenant = os.getenv("CHROMA_TENANT")
        database = os.getenv("CHROMA_DATABASE")

        if not api_key:
            raise RuntimeError(
                "CHROMA_API_KEY is missing in .env"
            )

        if not tenant:
            raise RuntimeError(
                "CHROMA_TENANT is missing in .env"
            )

        if not database:
            raise RuntimeError(
                "CHROMA_DATABASE is missing in .env"
            )

        _chroma = chromadb.CloudClient(
            api_key=api_key,
            tenant=tenant,
            database=database
        )

    return _chroma


def gemini_client():

    global _gemini

    if _gemini is None:

        api_key = os.getenv(
            "GEMINI_API_KEY"
        )

        if not api_key:

            raise RuntimeError(
                "GEMINI_API_KEY is missing in .env"
            )

        _gemini = genai.Client(
            api_key=api_key
        )

    return _gemini


# ============================================================
# LLM
# ============================================================

def llm(
    prompt: str,
    temperature: float = 0.2,
    max_tokens: int = 3000
) -> str:

    try:

        client = gemini_client()

        response = client.models.generate_content(

            model=LLM_MODEL,

            contents=prompt,

            config=types.GenerateContentConfig(

                temperature=temperature,

                max_output_tokens=max_tokens

            )
        )

        return (
            response.text or ""
        ).strip()

    except Exception as exc:

        raise RuntimeError(
            f"LLM API error: {exc}"
        ) from exc


# ============================================================
# REQUEST MODELS
# ============================================================

class ChatRequest(BaseModel):

    scope: str = "single"

    file_ids: List[str]

    question: str

    chat_history: List[Dict[str, str]] = []


class ReportRequest(BaseModel):

    scope: str = "single"

    file_ids: List[str]

    specifications: str = ""


# ============================================================
# GENERAL HELPERS
# ============================================================

def clean_name(value: Any) -> str:

    value = re.sub(
        r"\s+",
        "_",
        str(value).strip()
    )

    value = re.sub(
        r"[^A-Za-z0-9_]",
        "_",
        value
    )

    value = re.sub(
        r"_+",
        "_",
        value
    ).strip("_") or "column"

    if value[0].isdigit():

        value = "col_" + value

    return value


def normalize_columns(
    df: pd.DataFrame
) -> pd.DataFrame:

    df = df.copy()

    used = set()

    new_columns = []

    for col in df.columns:

        base = clean_name(col)

        name = base

        counter = 2

        while name.lower() in used:

            name = f"{base}_{counter}"

            counter += 1

        used.add(
            name.lower()
        )

        new_columns.append(
            name
        )

    df.columns = new_columns

    return df


# ============================================================
# TEXT FILE HANDLING
# ============================================================

def extract_text(
    data: bytes,
    filename: str
) -> str:

    name = filename.lower()

    # TXT / Markdown
    if name.endswith(
        (".txt", ".md")
    ):

        return data.decode(
            "utf-8",
            errors="ignore"
        )


    # PDF
    if name.endswith(".pdf"):

        parts = []

        with pymupdf.open(
            stream=data,
            filetype="pdf"
        ) as pdf:

            for page_number, page in enumerate(
                pdf,
                start=1
            ):

                text = page.get_text(
                    "text"
                ).strip()

                if text:

                    parts.append(
                        f"[Page {page_number}]\n{text}"
                    )

        return "\n\n".join(parts)


    # DOCX
    if name.endswith(".docx"):

        document = Document(
            io.BytesIO(data)
        )

        parts = [
            paragraph.text.strip()
            for paragraph in document.paragraphs
            if paragraph.text.strip()
        ]

        for table in document.tables:

            for row in table.rows:

                parts.append(
                    " | ".join(
                        cell.text.strip()
                        for cell in row.cells
                    )
                )

        return "\n".join(parts)


    raise ValueError(
        "Unsupported text file. "
        "Use PDF, DOCX, TXT or MD."
    )


def chunk_text(
    text: str,
    size: int = 400,
    overlap: int = 50
) -> List[str]:

    words = (
        re.sub(
            r"\n{3,}",
            "\n\n",
            text
        )
        .split()
    )

    chunks = []

    start = 0

    while start < len(words):

        end = min(
            start + size,
            len(words)
        )

        chunks.append(
            " ".join(
                words[start:end]
            )
        )

        if end >= len(words):

            break

        start = max(
            end - overlap,
            start + 1
        )

    return chunks


def text_collection_name(
    file_id: str
) -> str:

    clean_id = re.sub(
        r"[^A-Za-z0-9_-]",
        "",
        file_id
    )[:50]

    return f"text_{clean_id}"


def index_text(
    file_id: str,
    filename: str,
    text: str
) -> int:

    pieces = chunk_text(text)

    if not pieces:

        raise ValueError(
            "No readable text found."
        )

    collection = (
        chroma_client()
        .get_or_create_collection(
            text_collection_name(
                file_id
            )
        )
    )

    embeddings = (
        embedding_model()
        .encode(
            pieces,
            batch_size=32,
            normalize_embeddings=True,
            show_progress_bar=False
        )
        .tolist()
    )

    ids = [
        f"{file_id}_{i}"
        for i in range(
            len(pieces)
        )
    ]

    metadata = [

        {
            "source": filename,
            "chunk": i
        }

        for i in range(
            len(pieces)
        )

    ]

    collection.upsert(

        ids=ids,

        documents=pieces,

        embeddings=embeddings,

        metadatas=metadata

    )

    return len(pieces)


def retrieve_text_chunks(
    file_id: str,
    question: str,
    k: int = 6
):

    collection = (
        chroma_client()
        .get_or_create_collection(
            text_collection_name(
                file_id
            )
        )
    )

    if collection.count() == 0:

        return []


    query_embedding = (
        embedding_model()
        .encode(
            [question],
            normalize_embeddings=True,
            show_progress_bar=False
        )[0]
        .tolist()
    )


    results = collection.query(

        query_embeddings=[
            query_embedding
        ],

        n_results=min(
            k,
            collection.count()
        )

    )


    documents = (
        results
        .get("documents", [[]])[0]
    )

    metadatas = (
        results
        .get("metadatas", [[]])[0]
    )


    return list(
        zip(
            documents,
            metadatas
        )
    )


# ============================================================
# DATASET HANDLING
# ============================================================

def load_dataset(
    data: bytes,
    filename: str
) -> pd.DataFrame:

    buffer = io.BytesIO(data)

    if filename.lower().endswith(".csv"):

        df = pd.read_csv(
            buffer
        )

    elif filename.lower().endswith(
        (".xlsx", ".xls")
    ):

        df = pd.read_excel(
            buffer
        )

    else:

        raise ValueError(
            "Unsupported dataset. "
            "Use CSV or Excel."
        )


    if df.empty:

        raise ValueError(
            "Dataset is empty."
        )


    return normalize_columns(
        df
    )


def profile_dataset(
    df: pd.DataFrame
) -> Dict[str, Any]:

    numeric_columns = (
        df
        .select_dtypes(
            include=np.number
        )
        .columns
        .tolist()
    )

    missing = df.isna().sum()

    return {

        "rows": int(
            len(df)
        ),

        "columns": int(
            len(df.columns)
        ),

        "column_names":
            list(df.columns),

        "numeric_columns":
            numeric_columns,

        "dtypes": {
            column:
                str(df[column].dtype)

            for column in df.columns
        },

        "missing": {

            column:
                int(value)

            for column, value
            in missing[
                missing > 0
            ].items()

        },

        "duplicates":
            int(
                df.duplicated()
                .sum()
            )
    }


# ============================================================
# DATASET METADATA
# ============================================================
#
# IMPORTANT:
# This is your existing v2 architecture.
#
# It creates a metadata string.
# It does NOT create another Chroma collection.
# ============================================================

def metadata_for_dataset(
    filename: str,
    df: pd.DataFrame
) -> str:

    docs = [

        (
            f"Dataset: {filename}\n"
            f"Columns: "
            f"{', '.join(df.columns)}"
        )

    ]

    for col in df.columns:

        samples = (
            df[col]
            .dropna()
            .astype(str)
            .head(5)
            .tolist()
        )

        docs.append(

            f"Column: {col}\n"
            f"Type: {df[col].dtype}\n"
            f"Sample values: "
            f"{', '.join(samples)}\n"
            f"Missing: "
            f"{int(df[col].isna().sum())}"

        )

    return "\n\n".join(
        docs
    )


# ============================================================
# SQLITE
# ============================================================

def create_sql_connection(
    df: pd.DataFrame
):

    conn = sqlite3.connect(
        ":memory:",
        check_same_thread=False
    )

    df.to_sql(

        "data",

        conn,

        index=False,

        if_exists="replace"

    )

    return conn


def db_schema(
    conn
) -> str:

    rows = conn.execute(
        "PRAGMA table_info(data)"
    ).fetchall()

    columns = []

    for row in rows:

        columns.append(
            f"  {row[1]} {row[2]}"
        )

    return (
        "CREATE TABLE data (\n"
        +
        ",\n".join(columns)
        +
        "\n);"
    )


def safe_sql(
    sql: str
) -> str:

    sql = sql.strip()

    # Remove markdown fences
    sql = re.sub(
        r"```sql\s*",
        "",
        sql,
        flags=re.IGNORECASE
    )

    sql = re.sub(
        r"```\s*",
        "",
        sql
    )

    # Find SELECT
    match = re.search(
        r"(?is)\bSELECT\b.*",
        sql
    )

    if not match:

        raise ValueError(
            "No SELECT query found "
            "in LLM response."
        )

    sql = (
        match
        .group(0)
        .strip()
        .rstrip(";")
        .strip()
    )


    forbidden = [

        "insert ",

        "update ",

        "delete ",

        "drop ",

        "alter ",

        "create ",

        "attach ",

        "detach ",

        "pragma ",

        "vacuum ",

        "replace "

    ]


    lower_sql = sql.lower()


    if any(
        word in lower_sql
        for word in forbidden
    ):

        raise ValueError(
            "Unsafe SQL rejected."
        )


    if ";" in sql:

        raise ValueError(
            "Multiple SQL statements "
            "rejected."
        )


    return sql


# ============================================================
# SQL GENERATION
# ============================================================

def generate_sql(
    question: str,
    df: pd.DataFrame,
    conn,
    chat_context: str = ""
) -> str:

    prompt = f"""
You are an expert SQLite data analyst.

PREVIOUS CONVERSATION:
{chat_context}

CURRENT USER QUESTION:
{question}

DATABASE SCHEMA:
{db_schema(conn)}

AVAILABLE COLUMNS:
{json.dumps(list(df.columns), indent=2)}

RULES:
- Return ONLY one SQLite SELECT statement.
- Use only table: data.
- Use only columns that exist.
- Never invent column names.
- For "next 2 years", "next 3 years", etc.,
  use the previous conversation to determine
  the reference year.
- Do not use Markdown.
- Do not explain the query.
- If this is not a data query, return NOT_SQL.
"""


    raw_sql = llm(

        prompt,

        temperature=0.0,

        max_tokens=1000

    )


    if "NOT_SQL" in raw_sql.upper():

        return "NOT_SQL"


    return safe_sql(
        raw_sql
    )


# ============================================================
# DATASET QUESTION
# ============================================================

def dataset_question(
    file_id: str,
    question: str,
    chat_context: str = ""
):

    item = FILES[file_id]

    df = item["dataframe"]

    metadata_context = item.get(
        "metadata_text",
        ""
    )

    conn = create_sql_connection(
        df
    )


    try:

        # ----------------------------------------------
        # Determine question type
        # ----------------------------------------------

        route = llm(

            f"""

Classify this dataset question
as exactly one word:

SQL
METADATA
BOTH


SQL:
Requires calculation, filtering,
ranking, aggregation, count,
comparison or other data operations.


METADATA:
Asks about meaning,
definitions or schema.


BOTH:
Requires metadata and calculation.


QUESTION:

{question}


Return only one word.
""",

            temperature=0.0,

            max_tokens=20

        ).strip().upper()


        sql = None

        result = None

        sql_context = ""


        # ==================================================
        # SQL / BOTH
        # ==================================================

        if route in {
            "SQL",
            "BOTH"
        }:

            try:

                sql = generate_sql(
                    question,
                    df,
                    conn,
                    chat_context
                )


                if sql != "NOT_SQL":

                    result = (
                        pd.read_sql_query(
                            sql,
                            conn
                        )
                    )


                    result_text = result.to_string(index=False)

                    sql_context = (
                        f"SQL:\n"
                        f"{sql}\n\n"
                        f"RESULT:\n"
                        f"{result_text}"
                    )


            except Exception as first_error:


                # ------------------------------------------
                # SQL REPAIR
                # ------------------------------------------

                repair_prompt = f"""

You are an expert SQLite debugger.

The following SQL query failed.


DATABASE SCHEMA:

{db_schema(conn)}


AVAILABLE COLUMNS:

{list(df.columns)}


USER QUESTION:

{question}

PREVIOUS CONVERSATION:

{chat_context}

FAILED SQL:

{sql}


DATABASE ERROR:

{first_error}


Generate a corrected SQLite SELECT query.


RULES:

- Use only table: data
- Use only columns in schema
- Do not modify data
- Return ONLY the SELECT query
- Do not use Markdown
- Do not explain the query

"""


                try:

                    repaired_sql = llm(

                        repair_prompt,

                        temperature=0.0,

                        max_tokens=1000

                    )


                    repaired_sql = safe_sql(
                        repaired_sql
                    )


                    result = (
                        pd.read_sql_query(
                            repaired_sql,
                            conn
                        )
                    )


                    sql = repaired_sql


                    sql_context = (

                        f"SQL:\n"
                        f"{sql}\n\n"

                        f"RESULT:\n"
                        f"{result.to_string(
                            index=False
                        )}"

                    )


                except Exception as second_error:

                    sql_context = (

                        "SQL could not be "
                        "executed.\n\n"

                        f"Initial error: "
                        f"{first_error}\n\n"

                        f"Repair error: "
                        f"{second_error}"

                    )


        # ==================================================
        # FINAL RESPONSE
        # ==================================================

        answer = llm(
    f"""
You are the final answer generator for DataPilot's
Dataset Chatbot.

Your job is to convert the COMPUTED RESULT into a
clear natural-language answer to the user's question.

PREVIOUS CONVERSATION:
{chat_context}

CURRENT USER QUESTION:
{question}

DATASET METADATA:
{metadata_context}

COMPUTED SQL RESULT:
{sql_context}

IMPORTANT RULES:

1. Answer the user in natural language.

2. NEVER return SQL.

3. NEVER return JSON.

4. NEVER return Python code.

5. NEVER return Markdown code fences.

6. NEVER describe how the SQL was generated.

7. NEVER ask the user to execute a query.

8. If the computed result contains multiple rows,
   summarize those rows directly.

9. Use the previous conversation only to understand
   follow-up references such as:
   "what about Punjab?"
   "give me the next 2 years"
   "compare it with Haryana."

10. The COMPUTED SQL RESULT is the source of truth
    for numerical answers.

11. Do not invent values that aren't present in the
    computed result.

12. If there is no usable computed result, clearly
    explain that the requested information could not
    be calculated.

13. Keep the answer concise and user-friendly.

Return ONLY the final natural-language answer.

Example:

User:
Which year had the highest revenue?

Computed result:
Year | Revenue
2020 | 1200
2021 | 1500

Good answer:
"Revenue was highest in 2021, at 1,500."

Bad answer:
SELECT ...
Bad answer:
{{"api": "sql_query", ...}}
"""
,
    temperature=0.0,
    max_tokens=1800
)


        return (
            answer,
            sql,
            result
        )


    finally:

        conn.close()


# ============================================================
# FILE VALIDATION
# ============================================================

def validate_file_ids(
    file_ids: List[str]
) -> List[str]:

    if not file_ids:

        raise HTTPException(

            status_code=400,

            detail="No files selected."

        )


    valid_ids = []


    for file_id in file_ids:

        if file_id not in FILES:

            raise HTTPException(

                status_code=404,

                detail=(
                    f"File {file_id} "
                    "not found."
                )

            )

        if file_id not in valid_ids:

            valid_ids.append(
                file_id
            )


    return valid_ids

def format_chat_history(
    history: List[Dict[str, str]]
) -> str:

    if not history:
        return "No previous conversation."

    recent_history = history[-6:]

    formatted = []

    for message in recent_history:

        role = message.get(
            "role",
            "user"
        )

        content = message.get(
            "content",
            ""
        )

        if role == "user":
            label = "USER"
        else:
            label = "ASSISTANT"

        formatted.append(
            f"{label}: {content}"
        )

    return "\n\n".join(
        formatted
    )


# ============================================================
# HEALTH
# ============================================================

@app.get("/api/health")
def health():

    return {

        "status": "ok",

        "model": LLM_MODEL,

        "embedding_model":
            EMBEDDING_MODEL,

        "uploaded_files":
            len(FILES),

        "storage_backend":
            os.getenv("STORAGE_BACKEND", "local")

    }


# ============================================================
# LIST UPLOADED FILES
# ============================================================

@app.get("/api/files")
def list_files():

    files = []


    for file_id, item in FILES.items():

        info = {

            "file_id":
                file_id,

            "filename":
                item["filename"],

            "kind":
                item["kind"]

        }


        if item["kind"] == "text":

            info["chunks"] = (
                item.get(
                    "chunks",
                    0
                )
            )

        else:

            df = item["dataframe"]

            info["rows"] = (
                len(df)
            )

            info["columns"] = (
                len(df.columns)
            )


        files.append(info)


    return {

        "files":
            files

    }


# ============================================================
# UPLOAD MULTIPLE FILES
# ============================================================

@app.post("/api/upload")
async def upload_files(
    files: List[UploadFile] = File(...)
):

    if not files:

        raise HTTPException(

            status_code=400,

            detail="No files uploaded."

        )


    uploaded = []


    for upload in files:

        filename = (
            upload.filename
            or "uploaded_file"
        )

        data = await upload.read()

        if len(data) > MAX_UPLOAD_SIZE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"{filename} exceeds the maximum upload size "
                    f"of {MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)} MB."
                )
            )

        suffix = (
            Path(filename)
            .suffix
            .lower()
        )

        if suffix not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Unsupported file format. Use PDF, DOCX, TXT, "
                    "MD, CSV or Excel."
                )
            )


        file_id = str(
            uuid.uuid4()
        )

        source_object = (
            f"uploads/{file_id}/{Path(filename).name}"
        )


        try:

            # ==========================================
            # TEXT FILE
            # ==========================================

            if suffix in {

                ".pdf",

                ".docx",

                ".txt",

                ".md"

            }:

                text = extract_text(
                    data,
                    filename
                )


                chunk_count = index_text(

                    file_id,

                    filename,

                    text

                )


                storage_path = put_bytes(
                    data,
                    source_object,
                    upload.content_type
                )

                FILES[file_id] = {

                    "filename":
                        filename,

                    "storage_path":
                        storage_path,

                    "kind":
                        "text",

                    "text":
                        text,

                    "chunks":
                        chunk_count

                }


                uploaded.append({

                    "file_id":
                        file_id,

                    "filename":
                        filename,

                    "kind":
                        "text",

                    "chunks":
                        chunk_count

                })


            # ==========================================
            # DATASET
            # ==========================================

            elif suffix in {

                ".csv",

                ".xlsx",

                ".xls"

            }:

                df = load_dataset(

                    data,

                    filename

                )


                metadata_text = (
                    metadata_for_dataset(
                        filename,
                        df
                    )
                )

                storage_path = put_bytes(
                    data,
                    source_object,
                    upload.content_type
                )


                FILES[file_id] = {

                    "filename":
                        filename,

                    "storage_path":
                        storage_path,

                    "kind":
                        "dataset",

                    "dataframe":
                        df,

                    "metadata_text":
                        metadata_text,

                    "profile":
                        profile_dataset(
                            df
                        )

                }


                uploaded.append({

                    "file_id":
                        file_id,

                    "filename":
                        filename,

                    "kind":
                        "dataset",

                    "rows":
                        len(df),

                    "columns":
                        len(df.columns)

                })


            # ==========================================
            # UNSUPPORTED
            # ==========================================

            else:

                raise ValueError(

                    "Unsupported file format. "
                    "Use PDF, DOCX, TXT, MD, "
                    "CSV or Excel."

                )


        except Exception as exc:

            # Remove any partial Chroma
            # collection created during indexing.

            try:

                client = chroma_client()

                try:

                    client.delete_collection(

                        text_collection_name(
                            file_id
                        )

                    )

                except Exception:
                    pass

            except Exception:
                pass

            try:
                delete_object(source_object)
            except Exception:
                pass


            raise HTTPException(

                status_code=400,

                detail=(
                    f"Could not process "
                    f"{filename}: {exc}"
                )

            )


    return {

        "status":
            "success",

        "files":
            uploaded

    }


# ============================================================
# DELETE ONE FILE
# ============================================================

@app.delete(
    "/api/files/{file_id}"
)
def delete_file(
    file_id: str
):

    if file_id not in FILES:

        raise HTTPException(

            status_code=404,

            detail="File not found."

        )


    client = chroma_client()


    try:

        client.delete_collection(

            text_collection_name(
                file_id
            )

        )

    except Exception:
        pass

    storage_path = FILES[file_id].get("storage_path")
    if storage_path:
        try:
            if storage_path.startswith("gs://"):
                parts = storage_path.split("/", 3)
                object_name = parts[3] if len(parts) == 4 else ""
            else:
                object_name = f"uploads/{file_id}/{Path(FILES[file_id]['filename']).name}"
            if object_name:
                delete_object(object_name)
        except Exception:
            pass



    return {

        "status":
            "deleted",

        "file_id":
            file_id

    }


# ============================================================
# CHAT: SINGLE + MULTI FILE
# ============================================================

@app.post("/api/chat")
def chat(
    request: ChatRequest
): 

    file_ids = validate_file_ids(
        request.file_ids
    )
    chat_context = format_chat_history(
    request.chat_history
    )


    if request.scope not in {
        "single",
        "multi"
    }:

        raise HTTPException(

            status_code=400,

            detail=(
                "scope must be "
                "'single' or 'multi'."
            )

        )


    # ========================================================
    # SINGLE FILE
    # ========================================================

    if request.scope == "single":

        if len(file_ids) != 1:

            raise HTTPException(

                status_code=400,

                detail=(
                    "Single-file mode "
                    "requires exactly "
                    "one file."
                )

            )


        file_id = file_ids[0]

        item = FILES[file_id]


        # ----------------------------------------------------
        # TEXT RAG
        # ----------------------------------------------------

        if item["kind"] == "text":

            retrieved = (
                retrieve_text_chunks(
                    file_id,
                    request.question
                )
            )


            if not retrieved:

                raise HTTPException(

                    status_code=400,

                    detail=(
                        "No relevant "
                        "document content "
                        "was found."
                    )

                )


            context = "\n\n".join(

                f"[{item['filename']}]"
                f"\n{doc}"

                for doc, meta
                in retrieved

            )


            answer = llm(
    f"""
You are DataPilot's
document RAG assistant.

PREVIOUS CONVERSATION:

{chat_context}

CURRENT QUESTION:

{request.question}

RETRIEVED CONTEXT:

{context}

RULES:

- Answer using the retrieved context.
- Use previous conversation only to
  understand references and follow-ups.
- Do not use previous answers as factual
  evidence when the document context
  contradicts them.
- Do not invent facts.
- If the context does not contain the
  answer, say so clearly.
""",
    temperature=0.1,
    max_tokens=1800
)


            return {

                "answer":
                    answer,

                "kind":
                    "text",

                "sources": [

                    {

                        "filename":
                            item[
                                "filename"
                            ],

                        "chunk":
                            meta.get(
                                "chunk"
                            )

                    }

                    for _, meta
                    in retrieved

                ],

                "file_ids":
                    file_ids

            }


        # ----------------------------------------------------
        # DATASET
        # ----------------------------------------------------

        answer, sql, result = dataset_question(
            file_id,
            request.question,
            chat_context
        )


        return {

            "answer":
                answer,

            "kind":
                "dataset",

            "sql":
                sql,

            "result": (

                result
                .replace({
                    np.nan:
                        None
                })
                .to_dict(
                    orient="records"
                )

                if result is not None

                else None

            ),

            "file_ids":
                file_ids

        }


    # ========================================================
    # MULTI FILE
    # ========================================================

    text_contexts = []

    dataset_parts = []


    for file_id in file_ids:

        item = FILES[file_id]


        # ----------------------------------------------------
        # TEXT
        # ----------------------------------------------------

        if item["kind"] == "text":

            retrieved = (
                retrieve_text_chunks(
                    file_id,
                    request.question
                )
            )


            context = "\n\n".join(

                f"[{item['filename']}]"
                f"\n{doc}"

                for doc, meta
                in retrieved

            )


            if context:

                sources = [

                    {

                        "filename":
                            item[
                                "filename"
                            ],

                        "chunk":
                            meta.get(
                                "chunk"
                            )

                    }

                    for _, meta
                    in retrieved

                ]


                text_contexts.append({

                    "filename":
                        item[
                            "filename"
                        ],

                    "context":
                        context,

                    "sources":
                        sources

                })


        # ----------------------------------------------------
        # DATASET
        # ----------------------------------------------------

        else:

            answer, sql, result = dataset_question(
                file_id,
                request.question,
                chat_context
            )


            result_rows = (

                result
                .replace({
                    np.nan:
                        None
                })
                .to_dict(
                    orient="records"
                )

                if result is not None

                else None

            )


            dataset_parts.append({

                "filename":
                    item[
                        "filename"
                    ],

                "answer":
                    answer,

                "sql":
                    sql,

                "result":
                    result_rows

            })


    # ========================================================
    # BUILD COMBINED EVIDENCE
    # ========================================================

    document_context = "\n\n---\n\n".join(

        f"""

FILE:
{part['filename']}


RETRIEVED DOCUMENT CONTEXT:

{part['context']}

"""

        for part
        in text_contexts

    )


    dataset_context = "\n\n---\n\n".join(

        f"""

FILE:
{part['filename']}


DATASET ANSWER:

{part['answer']}


GENERATED SQL:

{part['sql']}


COMPUTED RESULT:

{part['result']}

"""

        for part
        in dataset_parts

    )


    # ========================================================
    # FINAL MULTI-FILE LLM
    # ========================================================

    final_answer = llm(

        f"""

You are DataPilot in
MULTI-FILE MODE.


USER QUESTION:

{request.question}


DOCUMENT EVIDENCE:

{document_context}


DATASET EVIDENCE:

{dataset_context}


RULES:

1. Use only the supplied evidence.

2. Compare files when the
   question requires comparison.

3. Identify the source file
   when useful.

4. For numerical results,
   trust computed dataset
   results.

5. Never invent information.

6. If the files do not contain
   enough information, say so.

"""

        ,

        temperature=0.1,

        max_tokens=2500

    )


    all_sources = []


    for part in text_contexts:

        all_sources.extend(
            part["sources"]
        )


    return {

        "answer":
            final_answer,

        "kind":
            "multi-file",

        "sources":
            all_sources,

        "dataset_results":
            dataset_parts,

        "file_ids":
            file_ids

    }
    


# ============================================================
# REPORT CONTEXT
# ============================================================

def report_context_for_file(
    item: Dict[str, Any]
) -> str:

    # --------------------------------------------------------
    # TEXT
    # --------------------------------------------------------

    if item["kind"] == "text":

        return (

            f"SOURCE TYPE: "
            f"TEXT DOCUMENT\n"

            f"FILE: "
            f"{item['filename']}\n\n"

            f"DOCUMENT CONTENT:\n"

            f"{item['text'][:MAX_REPORT_CONTEXT_CHARS]}"

        )


    # --------------------------------------------------------
    # DATASET
    # --------------------------------------------------------

    df = item["dataframe"]

    profile = profile_dataset(
        df
    )

    numeric = (
        df
        .select_dtypes(
            include=np.number
        )
    )


    if not numeric.empty:

        statistics = (
            numeric
            .describe()
            .T
            .to_string()
        )

    else:

        statistics = (
            "No numeric columns."
        )


    return (

        f"SOURCE TYPE: "
        f"STRUCTURED DATASET\n"

        f"FILE: "
        f"{item['filename']}\n\n"

        f"DATASET PROFILE:\n"

        f"{json.dumps(
            profile,
            indent=2,
            default=str
        )}\n\n"

        f"DESCRIPTIVE STATISTICS:\n"

        f"{statistics}\n\n"

        f"SAMPLE ROWS:\n"

        f"{df.head(10).to_string(
            index=False
        )}"

    )


# ============================================================
# REPORT GENERATION
# ============================================================

def generate_report(
    file_ids: List[str],
    specifications: str
) -> str:

    contexts = "\n\n".join(

        """

==============================
SOURCE FILE
==============================


"""
        +
        report_context_for_file(
            FILES[file_id]
        )

        for file_id
        in file_ids

    )


    prompt = f"""

You are DataPilot,
a professional document
and data reporting assistant.


USER REPORT SPECIFICATIONS:

{
    specifications
    if specifications.strip()
    else
    "No custom specifications provided."
}


SOURCE MATERIAL:

{contexts}


Create a professional
Markdown report.


BASELINE SECTIONS:

# Report

## Executive Summary

## Source Overview

## Key Findings

## Analysis / Discussion

## Recommendations

## Conclusion


RULES:

1. Follow the user's
   specifications whenever
   possible.

2. For text documents,
   use only information
   supported by the documents.

3. For datasets, use supplied
   profiles, statistics and
   sample information.

4. Clearly identify the
   relevant source when
   multiple files are included.

5. Do not invent numbers,
   entities, trends or facts.

6. Keep the report suitable
   for a professional audience.

7. Be concise unless the user
   explicitly requests detail.

"""


    return llm(

        prompt,

        temperature=0.2,

        max_tokens=6500

    )


# ============================================================
# REPORT API
# ============================================================

@app.post("/api/report")
def report(
    request: ReportRequest
):

    file_ids = validate_file_ids(
        request.file_ids
    )


    if request.scope == "single":

        if len(file_ids) != 1:

            raise HTTPException(

                status_code=400,

                detail=(
                    "Single-file report "
                    "requires one file."
                )

            )


    try:

        content = generate_report(

            file_ids,

            request.specifications

        )


        report_id = str(
            uuid.uuid4()
        )


        REPORTS[report_id] = {

            "content":
                content,

            "file_ids":
                file_ids

        }


        return {

            "report_id":
                report_id,

            "content":
                content

        }


    except Exception as exc:

        raise HTTPException(

            status_code=500,

            detail=(
                f"Report generation "
                f"failed: {exc}"
            )

        )


# ============================================================
# PDF EXPORT
# ============================================================

def markdown_to_pdf(
    markdown_text: str,
    path: str
):

    styles = (
        getSampleStyleSheet()
    )


    title_style = ParagraphStyle(

        "TitleCustom",

        parent=styles["Title"],

        alignment=TA_CENTER,

        fontSize=18,

        spaceAfter=16

    )


    heading_style = ParagraphStyle(

        "HeadingCustom",

        parent=styles["Heading2"],

        fontSize=13,

        spaceBefore=10,

        spaceAfter=6

    )


    body_style = ParagraphStyle(

        "BodyCustom",

        parent=styles["BodyText"],

        fontSize=9.5,

        leading=14,

        spaceAfter=7

    )


    story = []


    for line in markdown_text.splitlines():

        line = line.strip()


        if not line:

            story.append(
                Spacer(1, 5)
            )


        elif line.startswith("# "):

            story.append(

                Paragraph(
                    line[2:],
                    title_style
                )

            )


        elif line.startswith("## "):

            story.append(

                Paragraph(
                    line[3:],
                    heading_style
                )

            )


        elif line.startswith("- "):

            story.append(

                Paragraph(
                    "• " + line[2:],
                    body_style
                )

            )


        else:

            safe = (

                line
                .replace(
                    "&",
                    "&amp;"
                )
                .replace(
                    "<",
                    "&lt;"
                )
                .replace(
                    ">",
                    "&gt;"
                )

            )


            safe = re.sub(

                r"\*\*(.*?)\*\*",

                r"<b>\1</b>",

                safe

            )


            story.append(

                Paragraph(
                    safe,
                    body_style
                )

            )


    SimpleDocTemplate(

        path,

        pagesize=A4,

        rightMargin=45,

        leftMargin=45,

        topMargin=45,

        bottomMargin=45

    ).build(
        story
    )


# ============================================================
# DOCX EXPORT
# ============================================================

def markdown_to_docx(
    markdown_text: str,
    path: str
):

    document = Document()


    for line in markdown_text.splitlines():

        line = line.strip()


        if not line:

            continue


        if line.startswith("# "):

            document.add_heading(
                line[2:],
                0
            )


        elif line.startswith("## "):

            document.add_heading(
                line[3:],
                1
            )


        elif line.startswith("### "):

            document.add_heading(
                line[4:],
                2
            )


        elif line.startswith("- "):

            document.add_paragraph(
                line[2:],
                style="List Bullet"
            )


        else:

            document.add_paragraph(
                line
            )


    document.save(path)


# ============================================================
# REPORT DOWNLOAD: PDF
# ============================================================

@app.get(
    "/api/report/{report_id}/pdf"
)
def report_pdf(
    report_id: str
):

    if report_id not in REPORTS:

        raise HTTPException(

            status_code=404,

            detail="Report not found."

        )


    tmp = tempfile.NamedTemporaryFile(

        delete=False,

        suffix=".pdf"

    )

    tmp.close()


    markdown_to_pdf(

        REPORTS[report_id]["content"],

        tmp.name

    )


    return FileResponse(

        tmp.name,

        media_type="application/pdf",

        filename="datapilot_report.pdf"

    )


# ============================================================
# REPORT DOWNLOAD: DOCX
# ============================================================

@app.get(
    "/api/report/{report_id}/docx"
)
def report_docx(
    report_id: str
):

    if report_id not in REPORTS:

        raise HTTPException(

            status_code=404,

            detail="Report not found."

        )


    tmp = tempfile.NamedTemporaryFile(

        delete=False,

        suffix=".docx"

    )

    tmp.close()


    markdown_to_docx(

        REPORTS[report_id]["content"],

        tmp.name

    )


    return FileResponse(

        tmp.name,

        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),

        filename="datapilot_report.docx"

    )


# ============================================================
# CLEAR CACHE
# ============================================================

@app.delete("/api/cache")
@app.delete("/api/cache")
def clear_cache():

    global FILES
    global REPORTS
    global _chroma

    # Clear in-memory state
    FILES = {}
    REPORTS = {}

    # Get the current Chroma Cloud client
    try:

        client = chroma_client()

        # Delete all DataPilot text collections
        collections = client.list_collections()

        for collection in collections:

            if collection.name.startswith("text_"):

                client.delete_collection(
                    collection.name
                )

        # Release cached client
        _chroma = None

        # Clear local uploaded objects
        local_objects = (
            BASE_DIR
            / "storage"
            / "objects"
        )

        if local_objects.exists():

            shutil.rmtree(
                local_objects
            )

        local_objects.mkdir(
            parents=True,
            exist_ok=True
        )

    except Exception as exc:

        raise HTTPException(

            status_code=500,

            detail=(
                "Could not fully "
                "clear DataPilot storage: "
                f"{exc}"
            )

        )

    return {

        "status":
            "cleared",

        "message":
            (
                "All uploaded files, "
                "indexed text, datasets, "
                "reports and DataPilot "
                "Chroma Cloud collections "
                "were cleared."
            )

    }
