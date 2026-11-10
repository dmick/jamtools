"""Tiny JSON API for the jam check-in page. Stores state in SQLite.

Run behind nginx:  gunicorn -w 2 -b 127.0.0.1:8081 app:app
Config (env):      CHECKIN_DB  path to the sqlite file (default /var/lib/jam-checkin/checkin.db)
"""
import json
import os
import re
import sqlite3
from flask import Flask, g, jsonify, request

DB_PATH = os.environ.get("CHECKIN_DB", "/var/lib/jam-checkin/checkin.db")
SCHEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

app = Flask(__name__)


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as db, open(SCHEMA) as f:
        db.executescript(f.read())


def clean(value, limit):
    """Return a trimmed string within the length limit, or None if invalid."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if 0 < len(value) <= limit else None


@app.get("/api/checkin/state")
def state():
    date = request.args.get("date", "")
    if not DATE_RE.match(date):
        return jsonify(error="bad date"), 400
    db = get_db()
    here = [r["name"] for r in db.execute("SELECT name FROM checkins WHERE event_date=?", (date,))]
    subs = {r["slot_key"]: r["name"] for r in db.execute("SELECT slot_key, name FROM subs WHERE event_date=?", (date,))}
    row = db.execute("SELECT titles FROM setlist_order WHERE event_date=?", (date,)).fetchone()
    return jsonify(here=here, subs=subs, order=json.loads(row["titles"]) if row else [])


@app.post("/api/checkin/here")
def here():
    body = request.get_json(silent=True) or {}
    date, name = body.get("date", ""), clean(body.get("name"), 100)
    if not DATE_RE.match(str(date)) or name is None:
        return jsonify(error="bad request"), 400
    db = get_db()
    if body.get("here"):
        db.execute("INSERT OR IGNORE INTO checkins (event_date, name) VALUES (?, ?)", (date, name))
    else:
        db.execute("DELETE FROM checkins WHERE event_date=? AND name=?", (date, name))
    db.commit()
    return jsonify(ok=True)


@app.post("/api/checkin/sub")
def sub():
    body = request.get_json(silent=True) or {}
    date, key = body.get("date", ""), clean(body.get("key"), 200)
    if not DATE_RE.match(str(date)) or key is None:
        return jsonify(error="bad request"), 400
    db = get_db()
    if body.get("name") is None:                      # null name = remove the override
        db.execute("DELETE FROM subs WHERE event_date=? AND slot_key=?", (date, key))
    else:
        name = clean(body.get("name"), 100)
        if name is None:
            return jsonify(error="bad request"), 400
        db.execute(
            "INSERT INTO subs (event_date, slot_key, name) VALUES (?, ?, ?) "
            "ON CONFLICT(event_date, slot_key) DO UPDATE SET name=excluded.name, updated_at=datetime('now')",
            (date, key, name),
        )
    db.commit()
    return jsonify(ok=True)


@app.post("/api/checkin/order")
def order():
    body = request.get_json(silent=True) or {}
    date, titles = body.get("date", ""), body.get("order")
    if (not DATE_RE.match(str(date)) or not isinstance(titles, list) or len(titles) > 200
            or not all(isinstance(t, str) and 0 < len(t) <= 200 for t in titles)):
        return jsonify(error="bad request"), 400
    db = get_db()
    if titles:
        db.execute(
            "INSERT INTO setlist_order (event_date, titles) VALUES (?, ?) "
            "ON CONFLICT(event_date) DO UPDATE SET titles=excluded.titles, updated_at=datetime('now')",
            (date, json.dumps(titles)),
        )
    else:                                             # empty = back to the original order
        db.execute("DELETE FROM setlist_order WHERE event_date=?", (date,))
    db.commit()
    return jsonify(ok=True)


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8081)
