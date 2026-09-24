FROM python:3.11-slim

WORKDIR /app

# libpq for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the ONNX models into the image. Otherwise every cold start on Render
# downloads them from Hugging Face before the first answer (~90MB each).
# Build with --build-arg BAKE_RERANKER=1 on instances with >=1GB RAM and set
# RERANKER_ENABLED=true.
ARG BAKE_RERANKER=0
ENV FASTEMBED_CACHE_PATH=/app/.models
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/all-MiniLM-L6-v2')" \
 && if [ "$BAKE_RERANKER" = "1" ]; then python -c "from fastembed.rerank.cross_encoder import TextCrossEncoder; TextCrossEncoder('Xenova/ms-marco-MiniLM-L-6-v2')"; fi

COPY . .
RUN chmod +x scripts/start.sh

EXPOSE 8000

CMD ["sh", "scripts/start.sh"]
