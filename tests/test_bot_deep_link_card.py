from jobfox import bot
from jobfox.apply import ApplyResult


def _job(**overrides) -> dict:
    base = dict(
        id=1,
        title="React Developer",
        company="Wildberries Bank",
        location="Remote",
        score=9,
        reason="Strong fit.",
        cover_letter="A cover letter.",
        recruiter_email=None,
        apply_url=None,
        url="https://t.me/easy_frontend_jobs/2267",
        cv_pdf=None,
        cv_pdf_filename=None,
    )
    base.update(overrides)
    return base


def _user(**overrides) -> dict:
    base = dict(tg_chat_id=123, cv_pdf=None, cv_pdf_filename=None)
    base.update(overrides)
    return base


def _capture_sends(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(
        bot.telegram_api, "send_message",
        lambda chat_id, text, **kw: sent.append({"chat_id": chat_id, "text": text, **kw}),
    )
    # gmail_link_url reads Settings().app_url, which requires a full env
    # config we don't want to stand up here — stub it, the compose URL's
    # exact value isn't under test.
    monkeypatch.setattr(bot.gmail_api, "gmail_link_url", lambda **kw: "https://mail.google.com/mail/?fake=1")
    return sent


def test_deep_link_card_offers_apply_url_button_when_no_email(monkeypatch):
    sent = _capture_sends(monkeypatch)
    job = _job(apply_url="https://perm.hh.ru/vacancy/134504808")
    user = _user()
    result = ApplyResult(
        outcome="deep_link", apply_id=1, to_email=None, subject="s", body="b",
    )

    bot.send_deep_link_card(user, job, result, chat_id=user["tg_chat_id"])

    card = sent[0]
    buttons = card["reply_markup"]["inline_keyboard"]
    assert buttons[0] == [{"text": "✅ Apply directly", "url": job["apply_url"]}]
    assert "Direct apply link found" in card["text"]


def test_deep_link_card_falls_back_to_compose_when_no_apply_url(monkeypatch):
    sent = _capture_sends(monkeypatch)
    job = _job(apply_url=None)
    user = _user()
    result = ApplyResult(
        outcome="deep_link", apply_id=2, to_email=None, subject="s", body="b",
    )

    bot.send_deep_link_card(user, job, result, chat_id=user["tg_chat_id"])

    card = sent[0]
    buttons = card["reply_markup"]["inline_keyboard"]
    assert buttons[0][0]["text"] == "📧 Compose in Gmail"
    assert "Add the recruiter email" in card["text"]


def test_deep_link_card_ignores_apply_url_when_recruiter_email_present(monkeypatch):
    sent = _capture_sends(monkeypatch)
    job = _job(recruiter_email="hr@acme.am", apply_url="https://hh.ru/vacancy/1")
    user = _user()
    result = ApplyResult(
        outcome="deep_link", apply_id=3, to_email="hr@acme.am", subject="s", body="b",
    )

    bot.send_deep_link_card(user, job, result, chat_id=user["tg_chat_id"])

    card = sent[0]
    buttons = card["reply_markup"]["inline_keyboard"]
    assert buttons[0][0]["text"] == "📧 Compose in Gmail"
