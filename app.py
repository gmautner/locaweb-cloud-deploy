"""Sample web application for Locaweb Cloud platform validation.

Exercises all platform features: PostgreSQL, filesystem blob storage,
and /up health check endpoint.
"""
import os
from datetime import datetime, timezone

from flask import Flask, request, redirect, url_for, render_template_string

app = Flask(__name__)

DB_CONFIGURED = bool(os.environ.get("POSTGRES_HOST", "").strip())

DB_CONFIG = {
    "host": os.environ.get("POSTGRES_HOST", "localhost"),
    "port": "5432",
    "dbname": os.environ.get("POSTGRES_DB", "app"),
    "user": os.environ.get("POSTGRES_USER", "app"),
    "password": os.environ.get("POSTGRES_PASSWORD", ""),
}

BLOB_PATH = os.environ.get("BLOB_STORAGE_PATH", "/data/blobs")

TEMPLATE = """<!DOCTYPE html>
<html>
<head><title>Locaweb Cloud Test App</title></head>
<body>
<h1>Locaweb Cloud Test App</h1>
<h2>Notes (PostgreSQL)</h2>
{% if db_status is none %}
<p><em>Database not configured</em></p>
{% elif db_status == false %}
<p><em>Database unavailable</em></p>
{% else %}
<form method="POST" action="/notes">
  <input name="content" placeholder="New note..." size="40" required>
  <button type="submit">Add</button>
</form>
<ul>
{% for note in notes %}
  <li>{{ note[1] }} <small>({{ note[2] }})</small></li>
{% endfor %}
</ul>
{% endif %}
<h2>Custom Environment Variables</h2>
<ul>
  <li><strong>MY_VAR:</strong> {{ my_var or '<em>not set</em>' }}</li>
  <li><strong>MY_SECRET:</strong> {{ my_secret or '<em>not set</em>' }}</li>
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
    import psycopg2
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
    if not DB_CONFIGURED:
        return "OK", 200
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
    notes = []
    db_status = None  # None = not configured, True = connected, False = error

    if DB_CONFIGURED:
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT id, content, created_at FROM notes ORDER BY id DESC LIMIT 20")
            notes = cur.fetchall()
            cur.close()
            conn.close()
            db_status = True
        except Exception:
            db_status = False

    files = []
    if os.path.isdir(BLOB_PATH):
        files = sorted(f for f in os.listdir(BLOB_PATH) if f != "lost+found")

    my_var = os.environ.get("MY_VAR", "")
    my_secret = os.environ.get("MY_SECRET", "")

    return render_template_string(TEMPLATE, notes=notes, files=files,
                                  my_var=my_var, my_secret=my_secret,
                                  db_status=db_status)


@app.route("/notes", methods=["POST"])
def add_note():
    if not DB_CONFIGURED:
        return redirect(url_for("index"))
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
    if DB_CONFIGURED:
        init_db()
    app.run(host="0.0.0.0", port=80)
