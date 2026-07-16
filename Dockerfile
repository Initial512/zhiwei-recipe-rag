FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/app/.cache/huggingface

WORKDIR /app

COPY Rag/requirements.txt /app/Rag/requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.6.0 \
    && python -m pip install --no-cache-dir -r /app/Rag/requirements.txt

COPY Rag /app/Rag
COPY data /app/data

# Download the embedding model while building the image so cold starts do not
# depend on a model download.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-zh-v1.5')"

RUN addgroup --system app && adduser --system --ingroup app app \
    && chown -R app:app /app

WORKDIR /app/Rag

EXPOSE 7860

USER app

CMD ["python", "-m", "uvicorn", "api:app", "--host", "0.0.0.0", "--port", "7860"]
