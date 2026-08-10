FROM python:3.11-slim

WORKDIR /app

# System deps for psycopg2 + sentence-transformers/torch
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x scripts/start.sh

EXPOSE 8000

CMD ["sh", "scripts/start.sh"]