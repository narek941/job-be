"""Auto-apply decision path in pipeline.run_for_user.

These tests stub out discovery, scoring, the LLM-backed tailor step, and the
Telegram-facing notify/card functions so only the should_auto gating + the
outcome-handling branch (success / deep_link downgrade / quota fallback) is
under test.
"""

from __future__ import annotations

import pytest

from jobfox import apply as apply_mod
from jobfox import pipeline


def _user(**overrides) -> dict:
    base = dict(
        id=1,
        tg_chat_id=123,
        name="Test User",
        email="test@example.com",
        locations=["Yerevan"],
        muted_companies=[],
        auto_apply=True,
        min_score_auto_apply=8,
        min_score_notify=6,
        cv_text="cv text",
        cv_profile=None,
        salary_min=None,
        salary_currency=None,
        desired_role=None,
    )
    base.update(overrides)
    return base


def _job(**overrides) -> dict:
    base = dict(
        id=10,
        location="Yerevan",
        company="Acme",
        url="https://example.com/job/10",
        cover_letter="A cover letter.",
        cv_tweaks={"bullets_to_add": [], "summary_rewrite": None},
        score=9,
        recruiter_email="hr@acme.com",
    )
    base.update(overrides)
    return base


@pytest.fixture
def update_job_calls(monkeypatch) -> list[tuple]:
    calls: list[tuple] = []
    monkeypatch.setattr(
        pipeline.db, "update_job",
        lambda job_id, **fields: calls.append((job_id, fields)),
    )
    return calls


@pytest.fixture(autouse=True)
def _stub_pipeline_stages(monkeypatch):
    """No discovery, no scoring, no real db.log_run/utcnow side effects."""
    monkeypatch.setattr(pipeline.discovery, "discover_for_user", lambda user: {})
    monkeypatch.setattr(pipeline, "_score_new_jobs", lambda user: 0)
    monkeypatch.setattr(pipeline.db, "log_run", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.db, "utcnow", lambda: "2026-06-30T00:00:00Z")


def _stub_candidate(monkeypatch, job: dict):
    monkeypatch.setattr(pipeline, "_candidates", lambda user: iter([job]))
    notify_calls: list[dict] = []
    card_calls: list[dict] = []
    monkeypatch.setattr(pipeline, "notify_match", lambda user, j: notify_calls.append(j))
    monkeypatch.setattr(
        pipeline, "send_deep_link_card",
        lambda user, j, result, *, chat_id: card_calls.append(
            {"job": j, "result": result, "chat_id": chat_id}
        ),
    )
    return notify_calls, card_calls


def test_auto_apply_success_counts_auto_applied(monkeypatch, update_job_calls):
    user = _user()
    job = _job()
    notify_calls, card_calls = _stub_candidate(monkeypatch, job)

    monkeypatch.setattr(
        apply_mod, "apply_to_job",
        lambda u, j: apply_mod.ApplyResult(
            outcome="gmail_draft", apply_id=1, to_email=j["recruiter_email"],
            subject="s", body="b", gmail_draft_id="d1", gmail_address="me@gmail.com",
        ),
    )

    result = pipeline.run_for_user(user)

    assert result.auto_applied == 1
    assert result.auto_failed == 0
    assert notify_calls == []
    assert card_calls == []


def test_auto_apply_deep_link_downgrades_job_and_sends_card(monkeypatch, update_job_calls):
    user = _user()
    job = _job()
    notify_calls, card_calls = _stub_candidate(monkeypatch, job)

    deep_link_result = apply_mod.ApplyResult(
        outcome="deep_link", apply_id=2, to_email=job["recruiter_email"],
        subject="s", body="b", needs_gmail_reauth=True,
    )
    monkeypatch.setattr(apply_mod, "apply_to_job", lambda u, j: deep_link_result)

    result = pipeline.run_for_user(user)

    assert result.auto_applied == 0
    assert result.auto_failed == 1
    assert (job["id"], {"status": "notified", "apply_error": None}) in update_job_calls
    assert len(card_calls) == 1
    assert card_calls[0]["result"] is deep_link_result
    assert card_calls[0]["chat_id"] == user["tg_chat_id"]
    assert notify_calls == []


def test_auto_apply_quota_exceeded_falls_back_to_notify(monkeypatch, update_job_calls):
    user = _user()
    job = _job()
    notify_calls, card_calls = _stub_candidate(monkeypatch, job)

    def _raise_quota(u, j):
        raise apply_mod.QuotaExceeded(tier="free", used=5, limit=5)

    monkeypatch.setattr(apply_mod, "apply_to_job", _raise_quota)

    result = pipeline.run_for_user(user)

    assert result.auto_applied == 0
    assert result.auto_failed == 0
    assert result.notified == 1
    assert notify_calls == [job]
    assert any(fields.get("status") == "notified" for _, fields in update_job_calls)
    assert card_calls == []


@pytest.mark.parametrize(
    "user_overrides,job_overrides",
    [
        ({"auto_apply": False}, {}),
        ({}, {"score": 5}),
        ({}, {"recruiter_email": None}),
    ],
    ids=["auto_apply_off", "score_below_threshold", "no_recruiter_email"],
)
def test_should_auto_gating_falls_back_to_notify(
    monkeypatch, update_job_calls, user_overrides, job_overrides,
):
    user = _user(**user_overrides)
    job = _job(**job_overrides)
    notify_calls, card_calls = _stub_candidate(monkeypatch, job)

    def _fail_if_called(u, j):
        raise AssertionError("apply_to_job should not be called when should_auto is False")

    monkeypatch.setattr(apply_mod, "apply_to_job", _fail_if_called)

    result = pipeline.run_for_user(user)

    assert result.auto_applied == 0
    assert result.auto_failed == 0
    assert result.notified == 1
    assert notify_calls == [job]
