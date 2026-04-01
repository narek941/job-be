"""Опционально создать тестового пользователя при старте (только для локальной разработки)."""

from __future__ import annotations

import logging
import os

from armapply.auth_deps import hash_password
from armapply.users_db import create_user, get_user_by_email, init_app_db
from armapply.workspace import ensure_user_workspace

log = logging.getLogger(__name__)


def maybe_seed_test_user() -> None:
    flag = os.environ.get("ARMAPPLY_SEED_TEST_USER", "").strip().lower()
    if flag not in ("1", "true", "yes"):
        return

    email = (os.environ.get("ARMAPPLY_TEST_USER_EMAIL") or "test@test.com").strip().lower()
    password = os.environ.get("ARMAPPLY_TEST_USER_PASSWORD") or "TestPass123"

    if len(password) < 8:
        log.warning("ARMAPPLY_SEED_TEST_USER: password must be at least 8 characters, skipping seed")
        return

    init_app_db()
    if get_user_by_email(email):
        log.info("Dev seed: user already exists (%s)", email)
        return

    uid = create_user(email, hash_password(password))
    ensure_user_workspace(uid)
    log.info("Dev seed: created %s (user id=%s) — use this to log in from the app", email, uid)
