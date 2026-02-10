"""Sample web application for Locaweb Cloud platform validation.

Exercises all platform features: PostgreSQL, filesystem blob storage,
and /up health check endpoint.
"""
import os
from datetime import datetime, timezone

from flask import Flask, request, redirect, url_for, render_template_string
import psycopg2

app = Flask(__name__)

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": os.environ.get("DB_PORT", "5432"),
    "dbname": os.environ.get("DB_NAME", "app"),
    "user": os.environ.get("DB_USERNAME", "app"),
    "password": os.environ.get("DB_PASSWORD", ""),
}

BLOB_PATH = os.environ.get("BLOB_STORAGE_PATH", "/data/blobs")

TEMPLATE = """<!DOCTYPE html>
<html>
<head><title>Locaweb Cloud Test App</title></head>
<body>
<h1>Locaweb Cloud Test App</h1>
<h2>Notes (PostgreSQL)</h2>
<form method="POST" action="/notes">
  <input name="content" placeholder="New note..." size="40" required>
  <button type="submit">Add</button>
</form>
<ul>
{% for note in notes %}
  <li>{{ note[1] }} <small>({{ note[2] }})</small></li>
{% endfor %}
</ul>
<h2>File Upload (Blob Storage)</h2>
<form method="POST" action="/upload" enctype="multipart/form-data">
  <input type="file" name="file" required>
  <button type="submit">Upload</button>
</form>
<ul>
{% for f in files %}
  <li>{{ f }}</li>
{% endfor %}
</ul>
</body>
</html>"""


def get_db():
    return psycopg2.connect(**DB_CONFIG)


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id SERIAL PRIMARY KEY,
            content TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    conn.commit()
    cur.close()
    conn.close()


@app.route("/up")
def health():
    """Health check endpoint required by kamal-proxy."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        return "OK", 200
    except Exception:
        return "DB unavailable", 503


@app.route("/")
def index():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, content, created_at FROM notes ORDER BY id DESC LIMIT 20")
    notes = cur.fetchall()
    cur.close()
    conn.close()

    files = []
    if os.path.isdir(BLOB_PATH):
        files = sorted(f for f in os.listdir(BLOB_PATH) if f != "lost+found")

    return render_template_string(TEMPLATE, notes=notes, files=files)


@app.route("/notes", methods=["POST"])
def add_note():
    content = request.form.get("content", "").strip()
    if content:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO notes (content) VALUES (%s)", (content,))
        conn.commit()
        cur.close()
        conn.close()
    return redirect(url_for("index"))


@app.route("/upload", methods=["POST"])
def upload():
    f = request.files.get("file")
    if f and f.filename:
        os.makedirs(BLOB_PATH, exist_ok=True)
        safe_name = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{f.filename}"
        f.save(os.path.join(BLOB_PATH, safe_name))
    return redirect(url_for("index"))


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=80)
