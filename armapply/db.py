"""PostgreSQL access layer.

One thread-local connection per worker thread, simple `query()` helper with
stale-connection retry, and a numbered migration runner. All schema changes
go in `_MIGRATIONS` — they are applied in order, exactly once, tracked in
`schema_migrations`.

Row types are TypedDicts so callers get autocomplete without an ORM.
"""

from __future__ import annotations

import json
import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Iterator, Literal, TypedDict
from urllib.parse import unquote, urlparse

import psycopg2
import psycopg2.extras

from armapply.config import settings

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Row types
# ---------------------------------------------------------------------------

JobStatus = Literal["new", "scored", "notified", "applied", "skipped", "muted", "failed"]
ApplyStatus = Literal["queued", "sent", "failed", "deep_link"]
JobSource = Literal["staff_am", "job_am", "myjob_am", "linkedin", "telegram"]


class User(TypedDict):
    id: int
    tg_chat_id: int
    email: str | None
    name: str | None
    cv_text: str | None
    cv_pdf: bytes | None
    cv_pdf_filename: str | None
    cv_profile: dict[str, Any] | None
    auto_apply: bool
    min_score_notify: int
    min_score_auto_apply: int
    worldwide_ratio: float
    queries: list[str]
    locations: list[str]
    telegram_channels: list[str]
    muted_companies: list[str]
    paused: bool
    # Gmail OAuth — set after the user runs /connect_gmail and grants the
    # `gmail.compose` scope. With these set, the apply flow creates a real
    # Gmail draft (To/Subject/Body + CV attached) instead of asking the user
    # to copy-paste. Both NULL = not connected.
    gmail_refresh_token: str | None
    gmail_address: str | None
    created_at: datetime
    updated_at: datetime


class Job(TypedDict):
    id: int
    user_id: int
    url: str
    url_hash: str
    source: JobSource
    title: str | None
    company: str | None
    location: str | None
    description: str | None
    salary: str | None
    recruiter_email: str | None
    score: int | None
    reason: str | None
    cover_letter: str | None
    cv_tweaks: dict[str, Any] | None
    status: JobStatus
    notified_at: datetime | None
    applied_at: datetime | None
    apply_error: str | None
    discovered_at: datetime


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

_local = threading.local()


def _connect() -> psycopg2.extensions.connection:
    raw = settings().database_url
    parsed = urlparse(raw)
    host = parsed.hostname
    port = parsed.port
    # Supabase pooler requires port 6543.
    if host and "pooler.supabase.com" in host and (port is None or port == 5432):
        port = 6543
    return psycopg2.connect(
        host=host,
        port=port or 5432,
        dbname=(parsed.path or "/postgres").lstrip("/"),
        user=parsed.username,
        password=unquote(parsed.password or ""),
        sslmode="require",
        connect_timeout=15,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


def _conn() -> psycopg2.extensions.connection:
    conn: psycopg2.extensions.connection | None = getattr(_local, "conn", None)
    if conn is None or conn.closed:
        conn = _connect()
        conn.autocommit = False
        _local.conn = conn
    return conn


@contextmanager
def transaction() -> Iterator[psycopg2.extensions.cursor]:
    """Run several statements atomically. Commits on success, rolls back on error."""
    conn = _conn()
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def query(
    sql: str,
    params: tuple[Any, ...] = (),
    *,
    fetch: Literal["none", "one", "all"] = "none",
) -> Any:
    """Run one statement. Retries once if the connection went stale."""
    for attempt in range(2):
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                result: Any
                if fetch == "one":
                    result = cur.fetchone()
                elif fetch == "all":
                    result = cur.fetchall()
                else:
                    result = cur.rowcount
            conn.commit()
            return result
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            if attempt == 0:
                log.warning("DB connection stale, retrying: %s", e)
                try:
                    conn.rollback()
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
                _local.conn = None
                continue
            raise
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
    raise RuntimeError("unreachable")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def url_hash(url: str) -> str:
    """Stable hash for deduplication. We hash the *cleaned* URL so listings
    that only differ in tracking params don't double-insert."""
    return sha256(url.strip().encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------

_MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS users (
            id                   SERIAL PRIMARY KEY,
            tg_chat_id           BIGINT UNIQUE NOT NULL,
            email                TEXT,
            cv_text              TEXT,
            cv_pdf               BYTEA,
            cv_pdf_filename      TEXT,
            auto_apply           BOOLEAN NOT NULL DEFAULT FALSE,
            min_score_notify     INT NOT NULL DEFAULT 6,
            min_score_auto_apply INT NOT NULL DEFAULT 8,
            worldwide_ratio      REAL NOT NULL DEFAULT 0.1,
            queries              TEXT[] NOT NULL DEFAULT '{}',
            locations            TEXT[] NOT NULL DEFAULT '{}',
            telegram_channels    TEXT[] NOT NULL DEFAULT '{}',
            muted_companies      TEXT[] NOT NULL DEFAULT '{}',
            paused               BOOLEAN NOT NULL DEFAULT FALSE,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id              SERIAL PRIMARY KEY,
            user_id         INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            url             TEXT NOT NULL,
            url_hash        TEXT NOT NULL,
            source          TEXT NOT NULL,
            title           TEXT,
            company         TEXT,
            location        TEXT,
            description     TEXT,
            salary          TEXT,
            recruiter_email TEXT,
            score           INT,
            reason          TEXT,
            cover_letter    TEXT,
            cv_tweaks       JSONB,
            status          TEXT NOT NULL DEFAULT 'new',
            notified_at     TIMESTAMPTZ,
            applied_at      TIMESTAMPTZ,
            apply_error     TEXT,
            discovered_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (user_id, url_hash)
        );

        CREATE INDEX IF NOT EXISTS jobs_user_status_idx ON jobs (user_id, status);
        CREATE INDEX IF NOT EXISTS jobs_user_score_idx  ON jobs (user_id, score DESC NULLS LAST);

        CREATE TABLE IF NOT EXISTS applies (
            id         SERIAL PRIMARY KEY,
            job_id     INT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            user_id    INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            to_email   TEXT,
            subject    TEXT NOT NULL,
            body       TEXT NOT NULL,
            cv_pdf     BYTEA,
            status     TEXT NOT NULL DEFAULT 'queued',
            error      TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            sent_at    TIMESTAMPTZ
        );

        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id         SERIAL PRIMARY KEY,
            user_id    INT REFERENCES users(id) ON DELETE CASCADE,
            stage      TEXT NOT NULL,
            status     TEXT NOT NULL,
            detail     TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """,
    ),
    (
        2,
        # Candidate's display name — passed into LLM prompts so it can't be
        # mistaken for a previous employer. NULL until the user runs /name.
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS name TEXT;",
    ),
    (
        3,
        # Structured CV profile extracted by the LLM at upload time.
        # Shape: {summary, headline, skills[], experience[{company, role,
        # from, to, bullets[]}], projects[{name, stack[], desc}], education[]}.
        # NULL until the first extraction succeeds.
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS cv_profile JSONB;",
    ),
    (
        4,
        # Gmail OAuth credentials per user. With these set the apply flow
        # creates a real Gmail draft in the user's account (Body + CV PDF
        # attached) instead of asking them to copy-paste. We store only the
        # refresh_token — access tokens are short-lived and re-minted on
        # demand. `gmail_address` is the Google-confirmed `From` so the
        # "Open Gmail drafts" deep link picks the right account.
        """
        ALTER TABLE users ADD COLUMN IF NOT EXISTS gmail_refresh_token TEXT;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS gmail_address TEXT;
        """,
    ),
]


def run_migrations() -> None:
    """Apply any unapplied migrations from `_MIGRATIONS`. Safe to call repeatedly."""
    with transaction() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
        )

    applied_rows = query("SELECT version FROM schema_migrations", fetch="all") or []
    applied: set[int] = {int(r["version"]) for r in applied_rows}

    for version, sql in _MIGRATIONS:
        if version in applied:
            continue
        log.info("Applying migration %d", version)
        with transaction() as cur:
            cur.execute(sql)
            cur.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s)", (version,)
            )


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------

def _row_to_user(row: dict[str, Any]) -> User:
    return User(**row)  # type: ignore[typeddict-item]


def get_user_by_chat(tg_chat_id: int) -> User | None:
    row = query(
        "SELECT * FROM users WHERE tg_chat_id = %s", (tg_chat_id,), fetch="one"
    )
    return _row_to_user(dict(row)) if row else None


def get_user(user_id: int) -> User | None:
    row = query("SELECT * FROM users WHERE id = %s", (user_id,), fetch="one")
    return _row_to_user(dict(row)) if row else None


def create_user(tg_chat_id: int) -> User:
    s = settings()
    row = query(
        "INSERT INTO users (tg_chat_id, worldwide_ratio, min_score_notify, "
        "min_score_auto_apply) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (tg_chat_id) DO UPDATE SET updated_at = NOW() RETURNING *",
        (
            tg_chat_id,
            s.worldwide_ratio_default,
            s.min_score_notify_default,
            s.min_score_auto_apply_default,
        ),
        fetch="one",
    )
    assert row is not None
    return _row_to_user(dict(row))


_USER_UPDATABLE = frozenset(
    {
        "email",
        "name",
        "cv_text",
        "cv_pdf",
        "cv_pdf_filename",
        "cv_profile",
        "auto_apply",
        "min_score_notify",
        "min_score_auto_apply",
        "worldwide_ratio",
        "queries",
        "locations",
        "telegram_channels",
        "muted_companies",
        "paused",
        "gmail_refresh_token",
        "gmail_address",
    }
)


def update_user(user_id: int, **fields: Any) -> None:
    if not fields:
        return
    bad = set(fields) - _USER_UPDATABLE
    if bad:
        raise ValueError(f"Cannot update fields: {sorted(bad)}")
    # JSONB columns expect a serialized string when passed via psycopg2.
    if "cv_profile" in fields and fields["cv_profile"] is not None:
        fields["cv_profile"] = json.dumps(fields["cv_profile"])
    assignments = ", ".join(f"{k} = %s" for k in fields)
    params = (*fields.values(), user_id)
    query(
        f"UPDATE users SET {assignments}, updated_at = NOW() WHERE id = %s", params
    )


def list_active_users() -> list[User]:
    """Users eligible for the daily pipeline: have a CV, queries, and aren't paused."""
    rows = query(
        "SELECT * FROM users WHERE NOT paused AND cv_text IS NOT NULL "
        "AND array_length(queries, 1) > 0",
        fetch="all",
    ) or []
    return [_row_to_user(dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# Job CRUD
# ---------------------------------------------------------------------------

def upsert_job(
    user_id: int,
    *,
    url: str,
    source: JobSource,
    title: str | None,
    company: str | None,
    location: str | None,
    description: str | None,
    salary: str | None = None,
    recruiter_email: str | None = None,
) -> tuple[int, bool]:
    """Insert if new (returns id, True). If exists, refresh metadata (returns id, False)."""
    h = url_hash(url)
    row = query(
        """
        INSERT INTO jobs (user_id, url, url_hash, source, title, company, location,
                          description, salary, recruiter_email)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, url_hash) DO UPDATE SET
            title           = COALESCE(EXCLUDED.title, jobs.title),
            company         = COALESCE(EXCLUDED.company, jobs.company),
            location        = COALESCE(EXCLUDED.location, jobs.location),
            description     = COALESCE(EXCLUDED.description, jobs.description),
            salary          = COALESCE(EXCLUDED.salary, jobs.salary),
            recruiter_email = COALESCE(EXCLUDED.recruiter_email, jobs.recruiter_email)
        RETURNING id, (xmax = 0) AS inserted
        """,
        (user_id, url, h, source, title, company, location, description, salary, recruiter_email),
        fetch="one",
    )
    assert row is not None
    return int(row["id"]), bool(row["inserted"])


def get_job(job_id: int) -> Job | None:
    row = query("SELECT * FROM jobs WHERE id = %s", (job_id,), fetch="one")
    return _row_to_job(dict(row)) if row else None


def _row_to_job(row: dict[str, Any]) -> Job:
    return Job(**row)  # type: ignore[typeddict-item]


_JOB_UPDATABLE = frozenset(
    {
        "score",
        "reason",
        "cover_letter",
        "cv_tweaks",
        "status",
        "notified_at",
        "applied_at",
        "apply_error",
        "recruiter_email",
    }
)


def update_job(job_id: int, **fields: Any) -> None:
    if not fields:
        return
    bad = set(fields) - _JOB_UPDATABLE
    if bad:
        raise ValueError(f"Cannot update fields: {sorted(bad)}")
    if "cv_tweaks" in fields and fields["cv_tweaks"] is not None:
        fields["cv_tweaks"] = json.dumps(fields["cv_tweaks"])
    assignments = ", ".join(f"{k} = %s" for k in fields)
    query(f"UPDATE jobs SET {assignments} WHERE id = %s", (*fields.values(), job_id))


def list_new_jobs(user_id: int) -> list[Job]:
    rows = query(
        "SELECT * FROM jobs WHERE user_id = %s AND status = 'new' ORDER BY id ASC",
        (user_id,),
        fetch="all",
    ) or []
    return [_row_to_job(dict(r)) for r in rows]


def list_jobs_to_notify(user_id: int, min_score: int) -> list[Job]:
    rows = query(
        "SELECT * FROM jobs WHERE user_id = %s AND status = 'scored' AND score >= %s "
        "ORDER BY score DESC, id ASC",
        (user_id, min_score),
        fetch="all",
    ) or []
    return [_row_to_job(dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# Pipeline run log
# ---------------------------------------------------------------------------

def log_run(user_id: int | None, stage: str, status: str, detail: str = "") -> None:
    query(
        "INSERT INTO pipeline_runs (user_id, stage, status, detail) VALUES (%s, %s, %s, %s)",
        (user_id, stage, status, detail[:2000] or None),
    )
