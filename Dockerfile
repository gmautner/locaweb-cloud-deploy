FROM python:3.12-slim
LABEL service="locaweb-e2e-test"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

RUN mkdir -p /data/blobs

EXPOSE 80

CMD ["sh", "-c", "if [ -n \"$POSTGRES_HOST\" ]; then for i in $(seq 1 30); do python -c 'from app import init_db; init_db()' && break || sleep 2; done; fi; exec gunicorn --bind 0.0.0.0:80 --workers 2 app:app"]
