# syntax=docker/dockerfile:1

# ---- build: wheels and the baked ONNX model -----------------------------
FROM python:3.11-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt

# Bake the ONNX models into the image. Otherwise every cold start downloads
# them from Hugging Face before the first answer (~90MB each). Build with
# --build-arg BAKE_RERANKER=1 on instances with >=1GB RAM and set
# RERANKER_ENABLED=true.
ARG BAKE_RERANKER=0
ENV FASTEMBED_CACHE_PATH=/models PYTHONPATH=/install/lib/python3.11/site-packages
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/all-MiniLM-L6-v2')" \
 && if [ "$BAKE_RERANKER" = "1" ]; then python -c "from fastembed.rerank.cross_encoder import TextCrossEncoder; TextCrossEncoder('Xenova/ms-marco-MiniLM-L-6-v2')"; fi

# ---- runtime -------------------------------------------------------------
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FASTEMBED_CACHE_PATH=/app/.models
# libpq for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=build /install /usr/local
COPY --from=build --chown=app:app /models /app/.models
COPY --chown=app:app . .
RUN chmod +x scripts/start.sh
USER app

EXPOSE 8000
CMD ["sh", "scripts/start.sh"]
