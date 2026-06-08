"""Telegram bot handlers.

The bot is the *only* user interface. State machine is small:
  /start                       register / show help
  send PDF document            uploaded as CV
  /queries kw1, kw2, kw3       set search queries
  /locations a, b, c           set LinkedIn locations (worldwide pool)
  /channels @c1 @c2            subscribe to telegram channels
  /worldwide 0.1               worldwide_ratio (0..1)
  /auto on|off                 toggle auto-apply
  /pause | /resume             pause/resume the daily pipeline
  /me                          show current settings
  /run                         run the pipeline now (debug)

Plus inline-button callbacks attached to each match notification:
  ✅ Apply · ⏭ Skip · 🔕 Mute · 🔗 Link
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from armapply import apply as apply_mod
from armapply import db, telegram_api

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Update parsing
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class IncomingMessage:
    chat_id: int
    text: str
    document_file_id: str | None
    document_filename: str | None


@dataclass(frozen=True, slots=True)
class IncomingCallback:
    callback_id: str
    chat_id: int
    message_id: int
    data: str


def parse_update(update: dict[str, Any]) -> IncomingMessage | IncomingCallback | None:
    if "callback_query" in update:
        cq = update["callback_query"]
        msg = cq.get("message") or {}
        chat = msg.get("chat") or {}
        return IncomingCallback(
            callback_id=str(cq["id"]),
            chat_id=int(chat.get("id", 0)),
            message_id=int(msg.get("message_id", 0)),
            data=str(cq.get("data", "")),
        )
    if "message" in update:
        m = update["message"]
        chat = m.get("chat") or {}
        doc = m.get("document") or {}
        return IncomingMessage(
            chat_id=int(chat.get("id", 0)),
            text=str(m.get("text", "")).strip(),
            document_file_id=doc.get("file_id"),
            document_filename=doc.get("file_name"),
        )
    return None


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------

def _md_escape(text: str) -> str:
    """Escape for Telegram Markdown (legacy mode)."""
    return (
        text.replace("\\", "\\\\")
            .replace("_", "\\_")
            .replace("*", "\\*")
            .replace("[", "\\[")
            .replace("`", "\\`")
    )


def _match_message(job: db.Job) -> str:
    title = _md_escape(job["title"] or "(untitled)")
    company = _md_escape(job["company"] or "—")
    location = _md_escape(job["location"] or "—")
    score = job["score"] or 0
    reason = _md_escape((job["reason"] or "").strip())
    cover = (job["cover_letter"] or "").strip()
    # Telegram's hard limit is 4096; the rest of the card uses ~400 chars,
    # so 3500 leaves a safety margin and fits virtually every cover letter
    # the LLM produces (cover_letter() asks for 180-260 words ≈ 1500 chars).
    cover_preview = cover if len(cover) <= 3500 else cover[:3500] + "…"
    email_line = (
        f"\n📧 Recruiter: `{_md_escape(job['recruiter_email'])}`"
        if job["recruiter_email"]
        else "\n📧 No recruiter email — apply opens the listing."
    )
    return (
        f"*{title}*\n"
        f"🏢 {company}  ·  📍 {location}\n"
        f"⭐ Fit: *{score}/10*\n"
        f"_{reason}_\n"
        f"{email_line}\n\n"
        f"*Cover letter:*\n{_md_escape(cover_preview)}"
    )


def _match_keyboard(job: db.Job) -> dict[str, Any]:
    rows = [
        [
            {"text": "✅ Apply", "callback_data": f"apply:{job['id']}"},
            {"text": "⏭ Skip", "callback_data": f"skip:{job['id']}"},
        ],
        [
            {"text": "🔕 Mute company", "callback_data": f"mute:{job['id']}"},
            {"text": "🔗 Open", "url": job["url"]},
        ],
    ]
    return {"inline_keyboard": rows}


# ---------------------------------------------------------------------------
# Entry points (used by FastAPI webhook handler and by pipeline.notify_match)
# ---------------------------------------------------------------------------

def notify_match(user: db.User, job: db.Job) -> None:
    telegram_api.send_message(
        user["tg_chat_id"],
        _match_message(job),
        reply_markup=_match_keyboard(job),
        parse_mode="Markdown",
    )


def handle_update(update: dict[str, Any]) -> None:
    parsed = parse_update(update)
    if parsed is None:
        return
    if isinstance(parsed, IncomingCallback):
        _handle_callback(parsed)
    else:
        _handle_message(parsed)


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

_HELP = (
    "*ArmApply* — Armenia-first job hunter.\n\n"
    "Send me a PDF résumé to start. Then:\n"
    "  `/name Narek Kolyan`  (your name — used in cover letters)\n"
    "  `/email you@gmail.com`  (Reply-To address for apply emails)\n"
    "  `/cv`  show CV info / `/cv preview` show first lines\n"
    "  `/profile`  show parsed CV / `/profile rebuild` to re-parse\n"
    "  `/skills` list, `/skills add a, b`, `/skills remove a`\n"
    "  `/summary <text>` overwrite the profile summary\n"
    "  `/queries frontend, react, node`\n"
    "  `/locations Yerevan, Remote`\n"
    "  `/channels senior_frontender easy_frontend_jobs`\n"
    "  `/worldwide 0.1`  (share of worldwide jobs)\n"
    "  `/auto on` or `/auto off`  (auto-apply when score ≥ threshold)\n"
    "  `/pause` / `/resume`\n"
    "  `/me`  show settings\n"
    "  `/run`  run the pipeline now\n"
)


def _get_or_create_user(chat_id: int) -> db.User:
    existing = db.get_user_by_chat(chat_id)
    return existing or db.create_user(chat_id)


def _handle_message(msg: IncomingMessage) -> None:
    user = _get_or_create_user(msg.chat_id)

    # PDF upload → CV.
    if msg.document_file_id:
        _handle_cv_upload(user, msg.document_file_id, msg.document_filename or "cv.pdf")
        return

    text = msg.text.strip()
    if not text:
        return
    cmd, _, rest = text.partition(" ")
    rest = rest.strip()
    handler = _COMMANDS.get(cmd.lower())
    if handler:
        try:
            handler(user, rest)
        except CommandError as e:
            telegram_api.send_message(user["tg_chat_id"], f"⚠️ {e}")
        except Exception:
            log.exception("command failed: %s", cmd)
            telegram_api.send_message(user["tg_chat_id"], "⚠️ Internal error — try again.")
    else:
        telegram_api.send_message(user["tg_chat_id"], _HELP)


class CommandError(Exception):
    pass


def _cmd_start(user: db.User, _rest: str) -> None:
    telegram_api.send_message(user["tg_chat_id"], _HELP)


def _cmd_email(user: db.User, rest: str) -> None:
    addr = rest.strip().lower()
    if not addr or "@" not in addr or " " in addr:
        raise CommandError("Usage: /email you@example.com  (used as Reply-To on apply emails)")
    db.update_user(user["id"], email=addr)
    telegram_api.send_message(user["tg_chat_id"], f"✅ Email: {addr}")


def _cmd_profile(user: db.User, rest: str) -> None:
    """`/profile` show, `/profile rebuild` re-extract from current cv_text."""
    from armapply import profile as profile_mod

    fresh = db.get_user(user["id"])
    assert fresh is not None
    arg = rest.strip().lower()

    if arg == "rebuild":
        if not fresh["cv_text"]:
            raise CommandError("No CV stored — send a PDF first.")
        telegram_api.send_message(user["tg_chat_id"], "🔄 Re-parsing your CV…")
        try:
            parsed = profile_mod.extract_profile(fresh["cv_text"])
        except Exception as e:
            telegram_api.send_message(user["tg_chat_id"], f"⚠️ Parse failed: {e}")
            return
        db.update_user(user["id"], cv_profile=dict(parsed))
        telegram_api.send_message(user["tg_chat_id"], "✅ Profile rebuilt.\n\n" + profile_mod.render(parsed))
        return

    telegram_api.send_message(user["tg_chat_id"], profile_mod.render(fresh["cv_profile"]))


def _cmd_skills(user: db.User, rest: str) -> None:
    """`/skills` list, `/skills add a, b`, `/skills remove a`."""
    from armapply import profile as profile_mod

    fresh = db.get_user(user["id"])
    assert fresh is not None
    profile = fresh["cv_profile"] or {}

    parts = rest.strip().split(maxsplit=1)
    op = parts[0].lower() if parts else ""
    payload = parts[1] if len(parts) > 1 else ""

    if not op or op == "list":
        skills = profile.get("skills") or []
        msg = ("🛠 Skills:\n" + ", ".join(skills)) if skills else "No skills stored. /profile rebuild to extract."
        telegram_api.send_message(user["tg_chat_id"], msg)
        return

    if op == "add":
        items = [s.strip() for s in payload.split(",") if s.strip()]
        if not items:
            raise CommandError("Usage: /skills add React, TypeScript, Node.js")
        updated = profile_mod.add_skills(profile, items)
        db.update_user(user["id"], cv_profile=dict(updated))
        telegram_api.send_message(user["tg_chat_id"], "✅ Skills now:\n" + ", ".join(updated.get("skills") or []))
        return

    if op == "remove":
        items = [s.strip() for s in payload.split(",") if s.strip()]
        if not items:
            raise CommandError("Usage: /skills remove React")
        updated = profile_mod.remove_skills(profile, items)
        db.update_user(user["id"], cv_profile=dict(updated))
        telegram_api.send_message(user["tg_chat_id"], "✅ Skills now:\n" + ", ".join(updated.get("skills") or ["—"]))
        return

    raise CommandError("Usage: /skills [list|add a,b|remove a]")


def _cmd_summary(user: db.User, rest: str) -> None:
    """`/summary` show, `/summary <text>` replace."""
    from armapply import profile as profile_mod

    fresh = db.get_user(user["id"])
    assert fresh is not None
    profile = fresh["cv_profile"] or {}

    text = rest.strip()
    if not text:
        cur = profile.get("summary") or "—"
        telegram_api.send_message(user["tg_chat_id"], f"📝 Summary:\n{cur}\n\nTo replace: /summary <new text>")
        return

    updated = profile_mod.set_summary(profile, text)
    db.update_user(user["id"], cv_profile=dict(updated))
    telegram_api.send_message(user["tg_chat_id"], f"✅ Summary updated:\n{updated['summary']}")


def _cmd_cv(user: db.User, rest: str) -> None:
    """`/cv` — show stored CV info.  `/cv preview` — first 800 chars."""
    fresh = db.get_user(user["id"])
    assert fresh is not None
    text = fresh["cv_text"] or ""
    if not text:
        telegram_api.send_message(
            user["tg_chat_id"],
            "❌ No CV stored yet. Send me a PDF and I'll extract the text.",
        )
        return

    if rest.strip().lower() == "preview":
        snippet = text[:800] + ("…" if len(text) > 800 else "")
        telegram_api.send_message(user["tg_chat_id"], snippet)
        return

    pdf_size = len(fresh["cv_pdf"]) if fresh["cv_pdf"] else 0
    updated = fresh["updated_at"].strftime("%Y-%m-%d %H:%M UTC") if fresh["updated_at"] else "—"
    lines = [
        "📄 CV info",
        f"Filename: {fresh['cv_pdf_filename'] or '—'}",
        f"Extracted text: {len(text):,} chars",
        f"PDF size: {pdf_size:,} bytes",
        f"Updated: {updated}",
        "",
        "Replace it: send a new PDF (or paste-friendly).",
        "Preview the first lines: /cv preview",
    ]
    telegram_api.send_message(user["tg_chat_id"], "\n".join(lines))


def _cmd_name(user: db.User, rest: str) -> None:
    name = rest.strip()
    if not name:
        raise CommandError("Usage: /name Narek Kolyan")
    if len(name) > 120:
        raise CommandError("Name too long (max 120 chars).")
    db.update_user(user["id"], name=name)
    telegram_api.send_message(user["tg_chat_id"], f"✅ Name: {name}")


def _cmd_queries(user: db.User, rest: str) -> None:
    queries = [q.strip() for q in rest.split(",") if q.strip()]
    if not queries:
        raise CommandError("Usage: /queries python, backend, fastapi")
    db.update_user(user["id"], queries=queries)
    telegram_api.send_message(user["tg_chat_id"], f"✅ Queries: {', '.join(queries)}")


def _cmd_locations(user: db.User, rest: str) -> None:
    locations = [l.strip() for l in rest.split(",") if l.strip()]
    if not locations:
        raise CommandError("Usage: /locations Yerevan, Remote")
    db.update_user(user["id"], locations=locations)
    telegram_api.send_message(user["tg_chat_id"], f"✅ Locations: {', '.join(locations)}")


def _cmd_channels(user: db.User, rest: str) -> None:
    channels = [c.strip().lstrip("@") for c in rest.replace(",", " ").split() if c.strip()]
    db.update_user(user["id"], telegram_channels=channels)
    msg = f"✅ Channels: {', '.join('@' + c for c in channels)}" if channels else "✅ Channels cleared."
    telegram_api.send_message(user["tg_chat_id"], msg)


def _cmd_worldwide(user: db.User, rest: str) -> None:
    try:
        ratio = float(rest)
    except ValueError:
        raise CommandError("Usage: /worldwide 0.1  (between 0 and 1)")
    if not 0.0 <= ratio <= 1.0:
        raise CommandError("Ratio must be between 0 and 1.")
    db.update_user(user["id"], worldwide_ratio=ratio)
    telegram_api.send_message(user["tg_chat_id"], f"✅ Worldwide ratio: {ratio:.2f}")


def _cmd_auto(user: db.User, rest: str) -> None:
    val = rest.strip().lower()
    if val not in {"on", "off"}:
        raise CommandError("Usage: /auto on  |  /auto off")
    db.update_user(user["id"], auto_apply=(val == "on"))
    telegram_api.send_message(user["tg_chat_id"], f"✅ Auto-apply {'enabled' if val == 'on' else 'disabled'}")


def _cmd_pause(user: db.User, _rest: str) -> None:
    db.update_user(user["id"], paused=True)
    telegram_api.send_message(user["tg_chat_id"], "⏸ Paused. /resume to restart.")


def _cmd_resume(user: db.User, _rest: str) -> None:
    db.update_user(user["id"], paused=False)
    telegram_api.send_message(user["tg_chat_id"], "▶️ Resumed.")


def _cmd_me(user: db.User, _rest: str) -> None:
    fresh = db.get_user(user["id"])
    assert fresh is not None
    from armapply.config import settings as _settings
    smtp_status = "✅ ready" if _settings().smtp_configured else "❌ not configured (auto-apply disabled)"
    lines = [
        f"*Your settings*",
        f"Name: {fresh['name'] or '— set with /name'}",
        f"Email: {fresh['email'] or '— set with /email'}",
        f"CV: {'✅ loaded' if fresh['cv_text'] else '❌ missing — send a PDF'}",
        f"SMTP: {smtp_status}",
        f"Queries: {', '.join(fresh['queries']) or '—'}",
        f"Locations: {', '.join(fresh['locations']) or '—'}",
        f"Channels: {', '.join('@' + c for c in fresh['telegram_channels']) or '—'}",
        f"Muted companies: {', '.join(fresh['muted_companies']) or '—'}",
        f"Worldwide ratio: {fresh['worldwide_ratio']:.2f}",
        f"Auto-apply: {'on' if fresh['auto_apply'] else 'off'}  "
        f"(threshold ≥ {fresh['min_score_auto_apply']})",
        f"Notify threshold: ≥ {fresh['min_score_notify']}",
        f"Paused: {'yes' if fresh['paused'] else 'no'}",
    ]
    telegram_api.send_message(user["tg_chat_id"], "\n".join(lines))


def _cmd_run(user: db.User, _rest: str) -> None:
    # Lazy import: pipeline imports bot for notify_match; importing eagerly
    # would create a cycle at module load.
    from armapply import pipeline

    telegram_api.send_message(user["tg_chat_id"], "🚀 Running pipeline…")
    r = pipeline.run_for_user(user)
    summary = (
        f"Discovery: {sum(x.get('new', 0) for x in r.discovery.values())} new · "
        f"Scored: {r.scored} · Notified: {r.notified} · Auto-applied: {r.auto_applied}"
    )
    if r.errors:
        summary += f"\n⚠️ {len(r.errors)} error(s): {r.errors[0][:200]}"
    telegram_api.send_message(user["tg_chat_id"], summary)


_COMMANDS = {
    "/start": _cmd_start,
    "/help": _cmd_start,
    "/name": _cmd_name,
    "/email": _cmd_email,
    "/cv": _cmd_cv,
    "/profile": _cmd_profile,
    "/skills": _cmd_skills,
    "/summary": _cmd_summary,
    "/queries": _cmd_queries,
    "/locations": _cmd_locations,
    "/channels": _cmd_channels,
    "/worldwide": _cmd_worldwide,
    "/auto": _cmd_auto,
    "/pause": _cmd_pause,
    "/resume": _cmd_resume,
    "/me": _cmd_me,
    "/run": _cmd_run,
}


# ---------------------------------------------------------------------------
# CV upload
# ---------------------------------------------------------------------------

def _handle_cv_upload(user: db.User, file_id: str, filename: str) -> None:
    from armapply import match, profile as profile_mod

    telegram_api.send_message(user["tg_chat_id"], "📄 Reading your CV…")
    try:
        info = telegram_api.get_file(file_id)
        pdf_bytes = telegram_api.download_file(info["file_path"])
    except Exception as e:
        log.exception("cv download failed")
        telegram_api.send_message(user["tg_chat_id"], f"⚠️ Failed to download CV: {e}")
        return

    text = match.extract_cv_text(pdf_bytes)
    if len(text.strip()) < 100:
        telegram_api.send_message(
            user["tg_chat_id"],
            "⚠️ Couldn't extract enough text from that PDF. "
            "Try a different export, or paste text manually.",
        )
        return

    # Store the raw artifacts first so a profile-extraction failure doesn't
    # lose the upload.
    db.update_user(
        user["id"],
        cv_text=text,
        cv_pdf=pdf_bytes,
        cv_pdf_filename=filename,
    )
    telegram_api.send_message(
        user["tg_chat_id"],
        f"✅ CV stored ({len(text):,} chars). Parsing structured profile…",
    )

    try:
        parsed = profile_mod.extract_profile(text)
        db.update_user(user["id"], cv_profile=dict(parsed))
        telegram_api.send_message(
            user["tg_chat_id"],
            "✅ Profile parsed. Review with /profile — edit with /skills or /summary.",
        )
    except Exception as e:
        log.exception("profile extraction failed")
        telegram_api.send_message(
            user["tg_chat_id"],
            f"⚠️ CV stored but profile parsing failed: {e}. "
            "Run /profile rebuild later to retry.",
        )


# ---------------------------------------------------------------------------
# Callback buttons
# ---------------------------------------------------------------------------

def _handle_callback(cb: IncomingCallback) -> None:
    user = db.get_user_by_chat(cb.chat_id)
    if user is None:
        telegram_api.answer_callback(cb.callback_id, "Send /start first.", show_alert=True)
        return

    action, _, raw_job_id = cb.data.partition(":")
    try:
        job_id = int(raw_job_id)
    except ValueError:
        telegram_api.answer_callback(cb.callback_id, "Invalid action.")
        return

    job = db.get_job(job_id)
    if job is None or job["user_id"] != user["id"]:
        telegram_api.answer_callback(cb.callback_id, "Job not found.", show_alert=True)
        return

    handler = _CALLBACKS.get(action)
    if not handler:
        telegram_api.answer_callback(cb.callback_id, "Unknown action.")
        return
    try:
        handler(user, job, cb)
    except Exception:
        log.exception("callback %s failed", cb.data)
        telegram_api.answer_callback(cb.callback_id, "Error — try again.", show_alert=True)


def _cb_apply(user: db.User, job: db.Job, cb: IncomingCallback) -> None:
    if job["status"] == "applied":
        telegram_api.answer_callback(cb.callback_id, "Already applied.")
        return
    if not job["cover_letter"]:
        telegram_api.answer_callback(cb.callback_id, "No cover letter yet.", show_alert=True)
        return
    result = apply_mod.apply_to_job(user, job)
    if result.outcome == "sent":
        telegram_api.answer_callback(cb.callback_id, "Email sent.")
        telegram_api.edit_message_text(
            cb.chat_id, cb.message_id,
            _match_message(job) + f"\n\n✅ *Sent* — to `{_md_escape(result.to_email or '')}`",
            reply_markup={"inline_keyboard": [[{"text": "🔗 Open", "url": job["url"]}]]},
            parse_mode="Markdown",
        )
    else:
        # No recruiter email OR SMTP not configured → deep-link only.
        telegram_api.answer_callback(cb.callback_id, "Open the link to apply.")
        telegram_api.edit_message_text(
            cb.chat_id, cb.message_id,
            _match_message(job) + "\n\n🔗 *Apply manually* — see the link above.",
            reply_markup={"inline_keyboard": [[{"text": "🔗 Open", "url": job["url"]}]]},
            parse_mode="Markdown",
        )


def _cb_skip(user: db.User, job: db.Job, cb: IncomingCallback) -> None:
    db.update_job(job["id"], status="skipped")
    telegram_api.answer_callback(cb.callback_id, "Skipped.")
    telegram_api.edit_message_text(
        cb.chat_id, cb.message_id, _match_message(job) + "\n\n⏭ *Skipped*",
        reply_markup={"inline_keyboard": [[{"text": "🔗 Open", "url": job["url"]}]]},
        parse_mode="Markdown",
    )


def _cb_mute(user: db.User, job: db.Job, cb: IncomingCallback) -> None:
    company = (job["company"] or "").strip()
    if not company:
        telegram_api.answer_callback(cb.callback_id, "No company on this job.", show_alert=True)
        return
    muted = list(user["muted_companies"] or [])
    if company.lower() not in {m.lower() for m in muted}:
        muted.append(company)
        db.update_user(user["id"], muted_companies=muted)
    db.update_job(job["id"], status="muted")
    telegram_api.answer_callback(cb.callback_id, f"Muted {company}.")
    telegram_api.edit_message_text(
        cb.chat_id, cb.message_id, _match_message(job) + f"\n\n🔕 *{_md_escape(company)} muted*",
        reply_markup={"inline_keyboard": [[{"text": "🔗 Open", "url": job["url"]}]]},
        parse_mode="Markdown",
    )


_CALLBACKS = {
    "apply": _cb_apply,
    "skip": _cb_skip,
    "mute": _cb_mute,
}
