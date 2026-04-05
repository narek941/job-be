"""ArmApply application database (users, logs) — PostgreSQL/Supabase backend."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from urllib.parse import urlparse, unquote

import psycopg2
import psycopg2.extras

_local = threading.local()

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def _raw_db_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if url:
        return url
    # Fallback: build from individual parts
    host = os.environ.get("SUPABASE_DB_HOST", "db.sgmbcveoxfkgcmfkvxuh.supabase.co")
    password = os.environ.get("SUPABASE_DB_PASSWORD", "")
    return f"postgresql://postgres:{password}@{host}:5432/postgres"


def _conn() -> psycopg2.extensions.connection:
    if not hasattr(_local, "conn") or _local.conn is None or _local.conn.closed:
        raw = _raw_db_url()
        # psycopg2 doesn't handle %-encoded passwords in URL — decode first
        parsed = urlparse(raw)
        _local.conn = psycopg2.connect(
            host=parsed.hostname,
            port=parsed.port or 5432,
            dbname=parsed.path.lstrip("/"),
            user=parsed.username,
            password=unquote(parsed.password or ""),
            sslmode="require",
            sslrootcert="disable",  # required for Supabase pooler
            connect_timeout=10,
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        _local.conn.autocommit = False
    return _local.conn


def _exec(sql: str, params=(), fetch: str = "none"):
    """Run a statement, reconnecting on stale connection."""
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        if fetch == "one":
            row = cur.fetchone()
            conn.commit()
            return row
        elif fetch == "all":
            rows = cur.fetchall()
            conn.commit()
            return rows
        else:
            rowcount = cur.rowcount
            conn.commit()
            return rowcount
    except psycopg2.OperationalError:
        # stale connection — reset and retry once
        _local.conn = None
        conn = _conn()
        cur = conn.cursor()
        cur.execute(sql, params)
        if fetch == "one":
            row = cur.fetchone()
            conn.commit()
            return row
        elif fetch == "all":
            rows = cur.fetchall()
            conn.commit()
            return rows
        else:
            rowcount = cur.rowcount
            conn.commit()
            return rowcount


# ---------------------------------------------------------------------------
# Schema init
# ---------------------------------------------------------------------------

def init_app_db() -> None:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            telegram_chat_id TEXT,
            preferences_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            stage TEXT NOT NULL,
            status TEXT NOT NULL,
            detail TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS api_logs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER,
            method TEXT,
            path TEXT,
            status_code INTEGER,
            detail TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS calendar_events (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            title TEXT NOT NULL,
            starts_at TEXT NOT NULL,
            ends_at TEXT,
            job_url TEXT,
            notes TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS jobs (
            user_id               INTEGER NOT NULL REFERENCES users(id),
            url                   TEXT NOT NULL,
            title                 TEXT,
            salary                TEXT,
            description           TEXT,
            location              TEXT,
            site                  TEXT,
            strategy              TEXT,
            discovered_at         TEXT,
            full_description      TEXT,
            application_url       TEXT,
            detail_scraped_at     TEXT,
            detail_error          TEXT,
            application_email     TEXT,
            fit_score             INTEGER,
            score_reasoning       TEXT,
            scored_at             TEXT,
            tailored_resume_path  TEXT,
            tailored_resume_text  TEXT,
            tailored_at           TEXT,
            tailor_attempts       INTEGER DEFAULT 0,
            cover_letter_path     TEXT,
            cover_letter_text     TEXT,
            cover_letter_at       TEXT,
            cover_attempts        INTEGER DEFAULT 0,
            applied_at            TEXT,
            apply_status          TEXT,
            apply_error           TEXT,
            apply_attempts        INTEGER DEFAULT 0,
            agent_id              TEXT,
            last_attempted_at     TEXT,
            apply_duration_ms     INTEGER,
            apply_task_id         TEXT,
            verification_confidence TEXT,
            PRIMARY KEY (user_id, url)
        );
    """)
    # Add columns if they don't exist yet (safe migration)
    for col, typedef in [
        ("tailored_resume_text", "TEXT"),
        ("cover_letter_text",    "TEXT"),
    ]:
        try:
            _exec(f"ALTER TABLE jobs ADD COLUMN IF NOT EXISTS {col} {typedef}")
        except Exception:
            pass
    conn.commit()


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------

def create_user(email: str, password_hash: str) -> int:
    init_app_db()
    now = datetime.now(timezone.utc).isoformat()
    row = _exec(
        "INSERT INTO users (email, password_hash, preferences_json, created_at) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (email.lower(), password_hash, "{}", now),
        fetch="one",
    )
    return int(row["id"])


def get_user_by_email(email: str) -> dict | None:
    init_app_db()
    row = _exec("SELECT * FROM users WHERE email = %s", (email.lower(),), fetch="one")
    return dict(row) if row else None


def get_user_by_id(uid: int) -> dict | None:
    init_app_db()
    row = _exec("SELECT * FROM users WHERE id = %s", (uid,), fetch="one")
    return dict(row) if row else None


def update_user_telegram(user_id: int, chat_id: str | None) -> None:
    init_app_db()
    _exec("UPDATE users SET telegram_chat_id = %s WHERE id = %s", (chat_id, user_id))


def update_user_preferences(user_id: int, prefs: dict) -> None:
    init_app_db()
    _exec(
        "UPDATE users SET preferences_json = %s WHERE id = %s",
        (json.dumps(prefs), user_id),
    )


def list_all_user_ids() -> list[int]:
    init_app_db()
    rows = _exec("SELECT id FROM users", fetch="all")
    return [int(r["id"]) for r in rows] if rows else []


def get_user_preferences(user_id: int) -> dict:
    u = get_user_by_id(user_id)
    if not u:
        return {}
    try:
        return json.loads(u["preferences_json"] or "{}")
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# Pipeline runs
# ---------------------------------------------------------------------------

def log_pipeline_run(user_id: int, stage: str, status: str, detail: str | None = None) -> None:
    init_app_db()
    now = datetime.now(timezone.utc).isoformat()
    _exec(
        "INSERT INTO pipeline_runs (user_id, stage, status, detail, created_at) VALUES (%s, %s, %s, %s, %s)",
        (user_id, stage, status, detail, now),
    )


def list_pipeline_runs(user_id: int, limit: int = 50) -> list[dict]:
    init_app_db()
    rows = _exec(
        "SELECT * FROM pipeline_runs WHERE user_id = %s ORDER BY id DESC LIMIT %s",
        (user_id, limit),
        fetch="all",
    )
    return [dict(r) for r in rows] if rows else []


# ---------------------------------------------------------------------------
# Calendar events
# ---------------------------------------------------------------------------

def create_calendar_event(
    user_id: int,
    title: str,
    starts_at: str,
    ends_at: str | None,
    job_url: str | None,
    notes: str | None,
) -> int:
    init_app_db()
    now = datetime.now(timezone.utc).isoformat()
    row = _exec(
        "INSERT INTO calendar_events (user_id, title, starts_at, ends_at, job_url, notes, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (user_id, title, starts_at, ends_at, job_url, notes, now),
        fetch="one",
    )
    return int(row["id"])


def list_calendar_events(user_id: int, limit: int = 100) -> list[dict]:
    init_app_db()
    rows = _exec(
        "SELECT * FROM calendar_events WHERE user_id = %s ORDER BY starts_at ASC LIMIT %s",
        (user_id, limit),
        fetch="all",
    )
    return [dict(r) for r in rows] if rows else []


def get_calendar_event(user_id: int, event_id: int) -> dict | None:
    init_app_db()
    row = _exec(
        "SELECT * FROM calendar_events WHERE id = %s AND user_id = %s",
        (event_id, user_id),
        fetch="one",
    )
    return dict(row) if row else None


def delete_calendar_event(user_id: int, event_id: int) -> bool:
    init_app_db()
    rowcount = _exec(
        "DELETE FROM calendar_events WHERE id = %s AND user_id = %s",
        (event_id, user_id),
    )
    return rowcount > 0


# ---------------------------------------------------------------------------
# Jobs (Supabase-native)
# ---------------------------------------------------------------------------

def upsert_jobs_batch(user_id: int, jobs: list[dict], site: str, strategy: str) -> tuple[int, int]:
    """Store or update jobs in Supabase."""
    init_app_db()
    now = datetime.now(timezone.utc).isoformat()
    new = 0
    updated = 0
    
    for job in jobs:
        url = job.get("url")
        if not url: continue
        
        # Check if exists
        existing = _exec("SELECT url FROM jobs WHERE user_id = %s AND url = %s", (user_id, url), fetch="one")
        if existing:
            _exec(
                "UPDATE jobs SET title = %s, description = %s, location = %s, site = %s, strategy = %s WHERE user_id = %s AND url = %s",
                (job.get("title"), job.get("description"), job.get("location"), site, strategy, user_id, url)
            )
            updated += 1
        else:
            _exec(
                "INSERT INTO jobs (user_id, url, title, salary, description, location, site, strategy, discovered_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (user_id, url, job.get("title"), job.get("salary"), job.get("description"), job.get("location"), site, strategy, now)
            )
            new += 1
    return new, updated


def get_jobs_for_user(user_id: int, limit: int = 50, min_score: int | None = None) -> list[dict]:
    init_app_db()
    sql = "SELECT * FROM jobs WHERE user_id = %s"
    params = [user_id]
    if min_score is not None:
        sql += " AND fit_score >= %s"
        params.append(min_score)
    sql += " ORDER BY fit_score DESC NULLS LAST, discovered_at DESC LIMIT %s"
    params.append(limit)
    rows = _exec(sql, tuple(params), fetch="all")
    return [dict(r) for r in rows] if rows else []


def get_job_by_url(user_id: int, url: str) -> dict | None:
    init_app_db()
    row = _exec("SELECT * FROM jobs WHERE user_id = %s AND url = %s", (user_id, url), fetch="one")
    return dict(row) if row else None


def update_job_field(user_id: int, url: str, field: str, value: Any) -> None:
    init_app_db()
    # Be careful with dynamic field names in real production, but here we control 'field'
    _exec(f"UPDATE jobs SET {field} = %s WHERE user_id = %s AND url = %s", (value, user_id, url))


def get_jobs_stats_supabase(user_id: int) -> dict:
    init_app_db()
    total    = _exec("SELECT COUNT(*) FROM jobs WHERE user_id = %s", (user_id,), fetch="one")["count"]
    applied  = _exec("SELECT COUNT(*) FROM jobs WHERE user_id = %s AND applied_at IS NOT NULL", (user_id,), fetch="one")["count"]
    high_fit = _exec("SELECT COUNT(*) FROM jobs WHERE user_id = %s AND fit_score >= 7", (user_id,), fetch="one")["count"]
    return {
        "total":   total,
        "applied": applied,
        "tailored": high_fit,
    }


# ---------------------------------------------------------------------------
# Document storage (CV + Cover Letter text in Supabase)
# ---------------------------------------------------------------------------

def save_job_documents(
    user_id: int,
    url: str,
    cv_text: str,
    cover_text: str,
) -> None:
    """Persist tailored CV and cover letter TEXT directly in Supabase."""
    init_app_db()
    now = datetime.now(timezone.utc).isoformat()
    _exec(
        """
        UPDATE jobs
           SET tailored_resume_text = %s,
               cover_letter_text    = %s,
               tailored_at          = %s,
               cover_letter_at      = %s
         WHERE user_id = %s AND url = %s
        """,
        (cv_text, cover_text, now, now, user_id, url),
    )


def get_job_documents(user_id: int, url: str) -> dict:
    """Return the stored CV and cover letter texts for a job."""
    init_app_db()
    row = _exec(
        "SELECT tailored_resume_text, cover_letter_text FROM jobs WHERE user_id = %s AND url = %s",
        (user_id, url),
        fetch="one",
    )
    if not row:
        return {"cv_text": "", "cover_text": ""}
    return {
        "cv_text":    row["tailored_resume_text"] or "",
        "cover_text": row["cover_letter_text"]    or "",
    }
