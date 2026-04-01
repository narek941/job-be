#!/usr/bin/env python3
"""
Migrate existing SQLite users → Supabase PostgreSQL.
Run once:  python3 migrate_to_supabase.py
"""
import json, os, sqlite3, sys
from pathlib import Path

# Load env
env_file = Path(__file__).parent / ".env.test"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).parent))

from armapply.users_db import init_app_db, _exec
from armapply.config import APP_DB_PATH

print("→ Initialising Supabase schema…")
init_app_db()
print("✓ Schema ready")

# Read local SQLite
sqlite_path = str(APP_DB_PATH)
if not Path(sqlite_path).exists():
    print("No local SQLite found — nothing to migrate.")
    sys.exit(0)

print(f"→ Reading from {sqlite_path}…")
conn = sqlite3.connect(sqlite_path)
conn.row_factory = sqlite3.Row
users = [dict(r) for r in conn.execute("SELECT * FROM users").fetchall()]
runs  = [dict(r) for r in conn.execute("SELECT * FROM pipeline_runs").fetchall()]
events = [dict(r) for r in conn.execute("SELECT * FROM calendar_events").fetchall()]
conn.close()

print(f"  Found: {len(users)} users, {len(runs)} pipeline runs, {len(events)} calendar events")

# Migrate users
id_map: dict[int, int] = {}  # old_id → new_id
for u in users:
    existing = _exec("SELECT id FROM users WHERE email = %s", (u["email"],), fetch="one")
    if existing:
        print(f"  ✓ User already exists: {u['email']} → id={existing['id']}")
        id_map[u["id"]] = existing["id"]
        continue
    row = _exec(
        "INSERT INTO users (email, password_hash, telegram_chat_id, preferences_json, created_at)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (u["email"], u["password_hash"], u.get("telegram_chat_id"), u.get("preferences_json", "{}"), u["created_at"]),
        fetch="one",
    )
    new_id = row["id"]
    id_map[u["id"]] = new_id
    print(f"  ✓ Migrated user: {u['email']} → new id={new_id}")

# Migrate pipeline runs
for r in runs:
    new_uid = id_map.get(r["user_id"])
    if not new_uid:
        continue
    _exec(
        "INSERT INTO pipeline_runs (user_id, stage, status, detail, created_at) VALUES (%s, %s, %s, %s, %s)",
        (new_uid, r["stage"], r["status"], r.get("detail"), r["created_at"]),
    )
print(f"  ✓ Migrated {len(runs)} pipeline runs")

# Migrate calendar events
for e in events:
    new_uid = id_map.get(e["user_id"])
    if not new_uid:
        continue
    _exec(
        "INSERT INTO calendar_events (user_id, title, starts_at, ends_at, job_url, notes, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (new_uid, e["title"], e["starts_at"], e.get("ends_at"), e.get("job_url"), e.get("notes"), e["created_at"]),
    )
print(f"  ✓ Migrated {len(events)} calendar events")

print("\n🎉 Migration complete! Your Supabase DB is ready.")
print(f"   User ID mapping (old SQLite → new Supabase): {id_map}")
