from jobfox import bot


def _user(**overrides) -> dict:
    base = dict(id=1, tg_chat_id=123)
    base.update(overrides)
    return base


def _stats(**overrides) -> dict:
    base = dict(
        found=10, applied=5, replies=2, interviews=1, offers=0, rejections=1,
        auto_applied=0, auto_apply_needs_action=0,
    )
    base.update(overrides)
    return base


def _capture(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(bot.telegram_api, "send_message", lambda chat_id, text, **kw: sent.append(text))
    return sent


def test_stats_omits_auto_apply_section_when_no_auto_apply_activity(monkeypatch):
    monkeypatch.setattr(bot.db, "funnel_stats", lambda user_id, days: _stats())
    sent = _capture(monkeypatch)

    bot._cmd_stats(_user(), "")

    assert "Auto-applied" not in sent[0]
    assert "Needs your attention" not in sent[0]


def test_stats_shows_auto_applied_count(monkeypatch):
    monkeypatch.setattr(bot.db, "funnel_stats", lambda user_id, days: _stats(auto_applied=3))
    sent = _capture(monkeypatch)

    bot._cmd_stats(_user(), "")

    assert "🤖 Auto-applied: 3" in sent[0]
    assert "Needs your attention" not in sent[0]


def test_stats_flags_jobs_needing_attention(monkeypatch):
    monkeypatch.setattr(
        bot.db, "funnel_stats",
        lambda user_id, days: _stats(auto_applied=3, auto_apply_needs_action=2),
    )
    sent = _capture(monkeypatch)

    bot._cmd_stats(_user(), "")

    assert "⚠️ Needs your attention: 2" in sent[0]
