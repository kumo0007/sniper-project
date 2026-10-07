FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY docs ./docs

RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    RUN_WORKER=1 \
    SECURE_COOKIES=1 \
    DATABASE_URL=sqlite:////app/data/sniper.db

EXPOSE 8000

# Platforms inject PORT. Keep the worker inside this process for a single service.
CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
