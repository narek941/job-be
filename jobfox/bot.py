"""Telegram bot handlers.

The bot is the *only* user interface. State machine is small:
  /start                       register / show help
  send PDF document            uploaded as CV
  /queries kw1, kw2, kw3       set search queries
  /locations a, b, c           set LinkedIn locations (worldwide pool)
  /channels @c1 @c2            extra telegram channels (defaults always on)
  /worldwide 0.1               worldwide_ratio (0..1)
  /auto on|off                 toggle auto-apply
  /pause | /resume             pause/resume the daily pipeline
  /me                          show current settings
  /stats [days]                application funnel (found/applied/interview/offer)
  /run                         run the pipeline now (debug)

Plus inline-button callbacks attached to each match notification:
  ✅ Apply · ⏭ Skip · 🔕 Mute · 🔗 Link
and, once applied, manual outcome tracking:
  🎤 Interview · ❌ Rejected · 🎉 Offer
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from jobfox import analytics
from jobfox import apply as apply_mod
from jobfox import db, discovery, gmail_api, telegram_api

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


_SOURCE_LABELS: dict[str, str] = {
    "staff_am": "staff.am",
    "job_am": "job.am",
    "myjob_am": "myjob.am",
    "linkedin": "LinkedIn",
    "telegram": "Telegram",
}


def _match_message(job: db.Job) -> str:
    title = _md_escape(job["title"] or "(untitled)")
    company = _md_escape(job["company"] or "—")
    location = _md_escape(job["location"] or "—")
    score = job["score"] or 0
    reason = _md_escape((job["reason"] or "").strip())
    cover = (job["cover_letter"] or "").strip()
    desc = (job["description"] or "").strip()
    # Telegram's hard limit is 4096. Split the remaining budget (after
    # ~400 chars of meta lines/labels) between the listing description and
    # the cover letter so a long original posting can't push the cover
    # letter (the part that actually matters for Apply) off the card.
    desc_preview = desc if len(desc) <= 1200 else desc[:1200] + "…"
    cover_preview = cover if len(cover) <= 2200 else cover[:2200] + "…"
    desc_block = f"\n*Job description:*\n{_md_escape(desc_preview)}\n" if desc_preview else ""
    source = _SOURCE_LABELS.get(job["source"], job["source"])
    salary_line = f"\n💰 {_md_escape(job['salary'])}" if job.get("salary") else ""
    email_line = (
        f"\n📧 Recruiter: `{_md_escape(job['recruiter_email'])}`"
        if job["recruiter_email"]
        else "\n🔗 No recruiter email — direct apply link found."
        if job.get("apply_url")
        else "\n📧 No recruiter email — apply opens the listing."
    )
    return (
        f"*{title}*\n"
        f"🏢 {company}  ·  📍 {location}  ·  📡 {source}\n"
        f"⭐ Fit: *{score}/10*\n"
        f"_{reason}_\n"
        f"{salary_line}"
        f"{email_line}\n"
        f"{desc_block}\n"
        f"*Cover letter:*\n{_md_escape(cover_preview)}"
    )


def _outcome_row(job_id: int) -> list[dict[str, Any]]:
    """Manual funnel-tracking buttons, shown once a job is applied."""
    return [
        {"text": "🎤 Interview", "callback_data": f"interview:{job_id}"},
        {"text": "❌ Rejected", "callback_data": f"rejected:{job_id}"},
        {"text": "🎉 Offer", "callback_data": f"offer:{job_id}"},
    ]


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
    "*JobFox* — Armenia-first job hunter.\n\n"
    "Send me a PDF résumé to start. Then:\n"
    "  `/name Narek Kolyan`  (your name — used in cover letters)\n"
    "  `/email you@gmail.com`  (Reply-To address for apply emails)\n"
    "  `/connect_gmail`  hook up your Gmail so Apply creates real drafts\n"
    "  `/disconnect_gmail`  revoke the stored Gmail token\n"
    "  `/cv`  show CV info / `/cv preview` show first lines\n"
    "  `/profile`  show parsed CV / `/profile rebuild` to re-parse\n"
    "  `/skills` list, `/skills add a, b`, `/skills remove a`\n"
    "  `/summary <text>` overwrite the profile summary\n"
    "  `/role Senior Frontend Engineer`  (what you're hunting for)\n"
    "  `/salary 3000 USD`  (monthly minimum — low-ball listings score down)\n"
    "  `/portfolio https://github.com/you`  (linked in applications)\n"
    "  `/queries frontend, react, node`\n"
    "  `/locations Yerevan, Remote`\n"
    "  `/channels senior_frontender easy_frontend_jobs`  (extra channels —\n"
    "   @staffam, @Gortsiam, @bestjobinarmenia, @vgrecruitingit,\n"
    "   @djinni\\_jobs\\_bot are always on)\n"
    "  `/worldwide 0.1`  (share of worldwide jobs)\n"
    "  `/auto on` or `/auto off`  (auto-apply when score ≥ threshold)\n"
    "  `/pause` / `/resume`\n"
    "  `/me`  show settings\n"
    "  `/delete_me`  delete your account and all data\n"
    "  `/stats`  your application funnel (or `/stats 7` for a week)\n"
    "  `/run`  run the pipeline now\n\n"
    "After you apply, tap 🎤 Interview / ❌ Rejected / 🎉 Offer on the "
    "job card to track how it went.\n"
)


def _get_or_create_user(chat_id: int) -> db.User:
    existing = db.get_user_by_chat(chat_id)
    if existing:
        return existing
    user = db.create_user(chat_id)
    analytics.track(user["id"], "signup", {"surface": "telegram"})
    return user


def _handle_message(msg: IncomingMessage) -> None:
    user = _get_or_create_user(msg.chat_id)

    # PDF upload → CV.
    if msg.document_file_id:
        _handle_cv_upload(user, msg.document_file_id, msg.document_filename or "cv.pdf")
        analytics.track(user["id"], "cv_uploaded", {"surface": "telegram"})
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
    from jobfox.config import settings as _settings

    help_text = _HELP
    app_url = _settings().app_url.rstrip("/")
    if app_url:
        help_text += f"\nTerms: {app_url}/terms · Privacy: {app_url}/privacy"
    telegram_api.send_message(user["tg_chat_id"], help_text)


def _cmd_email(user: db.User, rest: str) -> None:
    addr = rest.strip().lower()
    if not addr or "@" not in addr or " " in addr:
        raise CommandError("Usage: /email you@example.com  (used as Reply-To on apply emails)")
    db.update_user(user["id"], email=addr)
    telegram_api.send_message(user["tg_chat_id"], f"✅ Email: {addr}")


def _cmd_profile(user: db.User, rest: str) -> None:
    """`/profile` show, `/profile rebuild` re-extract from current cv_text."""
    from jobfox import profile as profile_mod

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
    from jobfox import profile as profile_mod

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
    from jobfox import profile as profile_mod

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


def _cmd_role(user: db.User, rest: str) -> None:
    role = rest.strip()[:200]
    if not role:
        raise CommandError("Usage: /role Senior Frontend Engineer")
    db.update_user(user["id"], desired_role=role)
    telegram_api.send_message(user["tg_chat_id"], f"✅ Desired role: {role}")


_SALARY_RE = re.compile(r"^(\d[\d,. ]*)\s*([A-Za-z]{3})?$")


def _cmd_salary(user: db.User, rest: str) -> None:
    rest = rest.strip()
    if rest.lower() in {"off", "none", "clear"}:
        db.update_user(user["id"], salary_min=None)
        telegram_api.send_message(user["tg_chat_id"], "✅ Salary expectation cleared.")
        return
    m = _SALARY_RE.match(rest)
    if not m:
        raise CommandError(
            "Usage: /salary 3000 USD  (monthly minimum; AMD/USD/EUR/…)\n"
            "or /salary off to clear"
        )
    amount = int(re.sub(r"[,. ]", "", m.group(1)))
    currency = (m.group(2) or user["salary_currency"] or "USD").upper()
    db.update_user(user["id"], salary_min=amount, salary_currency=currency)
    telegram_api.send_message(
        user["tg_chat_id"],
        f"✅ Minimum salary: {amount:,} {currency}/month — matches clearly "
        "below this get scored down.",
    )


def _cmd_portfolio(user: db.User, rest: str) -> None:
    links = [l.strip() for l in rest.replace(",", " ").split() if l.strip()]
    bad = [l for l in links if not l.startswith(("http://", "https://"))]
    if bad:
        raise CommandError(f"Links must start with http(s):// — got {bad[0][:60]}")
    db.update_user(user["id"], portfolio_links=links[:10])
    msg = (
        "✅ Portfolio links (added to your applications):\n" + "\n".join(links[:10])
        if links
        else "✅ Portfolio links cleared."
    )
    telegram_api.send_message(user["tg_chat_id"], msg, disable_web_page_preview=True)


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
    requested = [c.strip().lstrip("@") for c in rest.replace(",", " ").split() if c.strip()]
    defaults = {discovery._tg_username(c).lower() for c in discovery.DEFAULT_TELEGRAM_CHANNELS}
    # Defaults are always scanned — only store what the user adds on top.
    extras = [c for c in requested if discovery._tg_username(c).lower() not in defaults]
    db.update_user(user["id"], telegram_channels=extras)
    lines = [
        "📡 Default channels (always on): "
        + ", ".join("@" + c for c in discovery.DEFAULT_TELEGRAM_CHANNELS),
        f"✅ Extra channels: {', '.join('@' + c for c in extras)}"
        if extras else "✅ No extra channels.",
    ]
    telegram_api.send_message(user["tg_chat_id"], "\n".join(lines))


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
    from jobfox.config import settings as _settings
    smtp_status = "✅ ready" if _settings().smtp_configured else "❌ not configured (auto-apply disabled)"
    gmail_status = (
        f"✅ {fresh.get('gmail_address')}"
        if fresh.get("gmail_refresh_token")
        else "❌ not connected — /connect_gmail to use drafts"
    )
    lines = [
        f"*Your settings*",
        f"Name: {fresh['name'] or '— set with /name'}",
        f"Email: {fresh['email'] or '— set with /email'}",
        f"CV: {'✅ loaded' if fresh['cv_text'] else '❌ missing — send a PDF'}",
        f"Gmail draft: {gmail_status}",
        f"SMTP: {smtp_status}",
        f"Plan: {fresh['tier']}  "
        f"({db.applies_this_week(fresh['id'])}/"
        f"{db.apply_quota(fresh['tier']) if db.apply_quota(fresh['tier']) is not None else '∞'} "
        f"applies this week)",
        f"Desired role: {fresh['desired_role'] or '— set with /role'}",
        "Salary: "
        + (
            f"≥ {fresh['salary_min']:,} {fresh['salary_currency']}/month"
            if fresh["salary_min"]
            else "— set with /salary"
        ),
        f"Portfolio: {', '.join(fresh['portfolio_links']) or '—'}",
        f"Queries: {', '.join(fresh['queries']) or '—'}",
        f"Locations: {', '.join(fresh['locations']) or '—'}",
        "Channels: defaults ("
        + ", ".join("@" + c for c in discovery.DEFAULT_TELEGRAM_CHANNELS)
        + ")" + (
            " + " + ", ".join("@" + c for c in fresh["telegram_channels"])
            if fresh["telegram_channels"] else ""
        ),
        f"Muted companies: {', '.join(fresh['muted_companies']) or '—'}",
        f"Worldwide ratio: {fresh['worldwide_ratio']:.2f}",
        f"Auto-apply: {'on' if fresh['auto_apply'] else 'off'}  "
        f"(threshold ≥ {fresh['min_score_auto_apply']})",
        f"Notify threshold: ≥ {fresh['min_score_notify']}",
        f"Paused: {'yes' if fresh['paused'] else 'no'}",
    ]
    telegram_api.send_message(user["tg_chat_id"], "\n".join(lines))


def _cmd_connect_gmail(user: db.User, _rest: str) -> None:
    """Send the user the OAuth consent URL as an inline-keyboard button.

    We must NOT inline the raw URL in a parse_mode='Markdown' message —
    the URL contains `_` (access_type=offline, include_granted_scopes,
    response_type, …) and Telegram's legacy Markdown parser treats those
    as italic markers, silently truncating the URL at the next `_`. The
    user ends up clicking a half-URL and Google rejects it with
    'Required parameter is missing: response_type'.

    Inline-keyboard `url` buttons are passed verbatim — no parsing — so
    they sidestep this entire class of bug.
    """
    from jobfox.config import settings as _settings

    if not _settings().gmail_oauth_configured:
        raise CommandError(
            "Gmail OAuth isn't configured on this deployment. "
            "Admin: set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / APP_URL."
        )
    try:
        url = gmail_api.make_oauth_url(user["tg_chat_id"])
    except gmail_api.GmailNotConnected as e:
        raise CommandError(str(e))
    fresh = db.get_user(user["id"])
    current = fresh and fresh.get("gmail_address")
    body = (
        (f"Currently connected as `{current}`. Re-authorizing will replace it.\n\n"
         if current else "")
        + "🔐 *Connect Gmail* — tap the button below (link valid 10 min).\n\n"
        "After you grant access I'll create real Gmail drafts on Apply — "
        "To/Subject/Body + your CV attached — ready to review and send."
    )
    telegram_api.send_message(
        user["tg_chat_id"],
        body,
        parse_mode="Markdown",
        reply_markup={
            "inline_keyboard": [[{"text": "🔐 Connect Gmail", "url": url}]]
        },
        disable_web_page_preview=True,
    )


def _cmd_disconnect_gmail(user: db.User, _rest: str) -> None:
    fresh = db.get_user(user["id"])
    if not fresh or not fresh.get("gmail_refresh_token"):
        telegram_api.send_message(user["tg_chat_id"], "ℹ️ Gmail isn't connected.")
        return
    db.update_user(user["id"], gmail_refresh_token=None, gmail_address=None)
    telegram_api.send_message(
        user["tg_chat_id"],
        "🔓 Gmail disconnected. (Also revoke at "
        "https://myaccount.google.com/permissions if you want to be thorough.)",
        disable_web_page_preview=True,
    )


def _cmd_stats(user: db.User, rest: str) -> None:
    try:
        days = max(1, min(365, int(rest))) if rest.strip() else 30
    except ValueError:
        raise CommandError("Usage: /stats  or  /stats 7  (days, 1-365)")
    s = db.funnel_stats(user["id"], days=days)

    def pct(num: int, den: int) -> str:
        return f"{num / den * 100:.1f}%" if den else "—"

    lines = [
        f"📊 *Last {days} days*",
        "",
        f"Jobs found: {s['found']}",
        f"Applications: {s['applied']}",
        f"Replies: {s['replies']}",
        f"Interviews: {s['interviews']}",
        f"Offers: {s['offers']}",
        f"Rejections: {s['rejections']}",
        "",
        f"Reply rate: {pct(s['replies'], s['applied'])}",
        f"Interview rate: {pct(s['interviews'], s['applied'])}",
        f"Offer rate: {pct(s['offers'], s['applied'])}",
    ]
    if s["auto_applied"] or s["auto_apply_needs_action"]:
        lines += [
            "",
            f"🤖 Auto-applied: {s['auto_applied']}",
        ]
        if s["auto_apply_needs_action"]:
            lines.append(
                f"⚠️ Needs your attention: {s['auto_apply_needs_action']} "
                "(Gmail/SMTP failed — sent as a manual card instead)"
            )
    telegram_api.send_message(user["tg_chat_id"], "\n".join(lines), parse_mode="Markdown")


def _cmd_delete_me(user: db.User, rest: str) -> None:
    if rest.strip().lower() != "confirm":
        telegram_api.send_message(
            user["tg_chat_id"],
            "⚠️ This permanently deletes your account: profile, CV, all jobs, "
            "applications and history. Your Gmail authorization is revoked. "
            "There is no undo.\n\n"
            "If you're sure, send:  `/delete_me confirm`",
            parse_mode="Markdown",
        )
        return
    gmail_api.revoke_token(user.get("gmail_refresh_token"))
    db.delete_user(user["id"])
    telegram_api.send_message(
        user["tg_chat_id"],
        "🗑 Done — your account and all data are deleted. "
        "Good luck out there; /start any time to begin fresh.",
    )


# /run cooldown — a /run loop would re-trigger discovery + LLM scoring on
# every call. In-memory is fine: single-process deploy, and a restart
# resetting the cooldown is harmless.
_RUN_COOLDOWN_SECONDS = 15 * 60
_last_run_at: dict[int, float] = {}


def _cmd_run(user: db.User, _rest: str) -> None:
    # Lazy import: pipeline imports bot for notify_match; importing eagerly
    # would create a cycle at module load.
    import time as _time

    from jobfox import pipeline

    last = _last_run_at.get(user["id"], 0.0)
    wait = _RUN_COOLDOWN_SECONDS - (_time.time() - last)
    if wait > 0:
        telegram_api.send_message(
            user["tg_chat_id"],
            f"⏳ The pipeline runs daily on its own — manual /run is limited. "
            f"Try again in {int(wait // 60) + 1} min.",
        )
        return
    _last_run_at[user["id"]] = _time.time()

    telegram_api.send_message(user["tg_chat_id"], "🚀 Running pipeline…")
    r = pipeline.run_for_user(user)
    summary = (
        f"Discovery: {sum(x.get('new', 0) for x in r.discovery.values())} new · "
        f"Scored: {r.scored} · Notified: {r.notified} · Auto-applied: {r.auto_applied}"
    )
    if r.auto_failed:
        summary += f" · ⚠️ Auto-apply needs action: {r.auto_failed}"
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
    "/role": _cmd_role,
    "/salary": _cmd_salary,
    "/portfolio": _cmd_portfolio,
    "/queries": _cmd_queries,
    "/locations": _cmd_locations,
    "/channels": _cmd_channels,
    "/worldwide": _cmd_worldwide,
    "/auto": _cmd_auto,
    "/pause": _cmd_pause,
    "/resume": _cmd_resume,
    "/me": _cmd_me,
    "/stats": _cmd_stats,
    "/run": _cmd_run,
    "/connect_gmail": _cmd_connect_gmail,
    "/disconnect_gmail": _cmd_disconnect_gmail,
    "/delete_me": _cmd_delete_me,
}


# ---------------------------------------------------------------------------
# CV upload
# ---------------------------------------------------------------------------

def _handle_cv_upload(user: db.User, file_id: str, filename: str) -> None:
    from jobfox import match, profile as profile_mod

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


def _draft_template_text(result: apply_mod.ApplyResult) -> str:
    """One Telegram-friendly code block containing the entire draft email.

    Mobile UX: long-press the block → Copy → paste into any email app.
    Wrapping everything in a single fence keeps it to one tap on mobile and
    one click on desktop. No Markdown is parsed inside ``` blocks so we
    don't need to escape the body.
    """
    to_line = f"To: {result.to_email}" if result.to_email else "To: <add recruiter email>"
    lines = [
        to_line,
        f"Subject: {result.subject}",
        "",
        result.body.strip(),
    ]
    return "```\n" + "\n".join(lines) + "\n```"


def _cb_apply(user: db.User, job: db.Job, cb: IncomingCallback) -> None:
    if job["status"] == "applied":
        telegram_api.answer_callback(cb.callback_id, "Already applied.")
        return
    if not job["cover_letter"]:
        telegram_api.answer_callback(cb.callback_id, "No cover letter yet.", show_alert=True)
        return
    try:
        result = apply_mod.apply_to_job(user, job)
    except apply_mod.QuotaExceeded as q:
        analytics.track(user["id"], "quota_hit", {"tier": q.tier, "limit": q.limit})
        from jobfox.config import settings as _settings

        app_url = _settings().app_url.rstrip("/")
        pricing = f"{app_url}/#pricing" if app_url else "the pricing page"
        telegram_api.answer_callback(cb.callback_id, "Weekly apply limit reached.")
        telegram_api.send_message(
            cb.chat_id,
            f"🦊 You've used all {q.limit} applies on the *{q.tier}* plan this "
            f"week. Upgrade for more firepower: {pricing}",
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        return
    analytics.track(user["id"], "apply", {"outcome": result.outcome, "source": job["source"]})
    if result.outcome == "sent":
        telegram_api.answer_callback(cb.callback_id, "Email sent.")
        telegram_api.edit_message_text(
            cb.chat_id, cb.message_id,
            _match_message(job) + f"\n\n✅ *Sent* — to `{_md_escape(result.to_email or '')}`"
            "\n_Track the outcome with the buttons below._",
            reply_markup={"inline_keyboard": [
                _outcome_row(job["id"]),
                [{"text": "🔗 Open", "url": job["url"]}],
            ]},
            parse_mode="Markdown",
        )
        return

    if result.outcome == "gmail_draft":
        # Real draft now sits in the user's Gmail account with the CV
        # attached. They review + send from Gmail; we deep-link them
        # straight there with `/u/<email>/` so multi-account folks land
        # in the right inbox.
        drafts_url = gmail_api.gmail_link_url(
            kind="drafts",
            gmail_address=result.gmail_address,
            draft_id=result.gmail_draft_id,
        )
        recipient_note = (
            f"to `{_md_escape(result.to_email)}`"
            if result.to_email
            else "(add the recruiter email before sending)"
        )
        telegram_api.answer_callback(cb.callback_id, "Draft created.")
        telegram_api.edit_message_text(
            cb.chat_id, cb.message_id,
            _match_message(job)
            + f"\n\n📝 *Draft in Gmail* — {recipient_note}. CV attached. "
              "Open Gmail to review and send.",
            reply_markup={"inline_keyboard": [
                [{"text": "📬 Open Gmail drafts", "url": drafts_url}],
                _outcome_row(job["id"]),
                [{"text": "🔗 Open listing", "url": job["url"]}],
            ]},
            parse_mode="Markdown",
        )
        return

    # deep_link path: hand the user a one-tap-copyable draft + the CV
    # attached as a Telegram document, so they can forward the whole
    # application from any email client (mobile included, where the Gmail
    # compose URL is awkward). A Gmail compose URL is still offered for the
    # desktop fast-path.
    send_deep_link_card(
        user, job, result,
        chat_id=cb.chat_id, edit_message_id=cb.message_id, callback_id=cb.callback_id,
    )


def send_deep_link_card(
    user: db.User,
    job: db.Job,
    result: apply_mod.ApplyResult,
    *,
    chat_id: int,
    edit_message_id: int | None = None,
    callback_id: str | None = None,
) -> None:
    """Render the deep-link fallback: note + compose-URL card, the
    copy-paste draft, and the CV attachment.

    Shared by a manual Apply tap (edits the existing match card via
    `edit_message_id`/`callback_id`) and an auto-apply transport failure
    (sends a fresh message — there's no callback context to edit)."""
    compose_url = gmail_api.gmail_link_url(
        kind="compose",
        to=result.to_email,
        subject=result.subject,
        body=result.body,
    )
    missing_recipient = not result.to_email
    apply_url = job.get("apply_url") if missing_recipient else None
    if apply_url:
        note = (
            "🔗 *Direct apply link found* — no recruiter email on this "
            "post. Cover letter + CV below in case the destination wants "
            "one pasted in."
        )
    elif missing_recipient:
        note = (
            "✏️ *Add the recruiter email* — none on the listing. Draft + CV "
            "below; paste into your email app."
        )
    else:
        note = (
            "📝 *Draft ready.* Tap-hold the block below to copy, paste it "
            "into your email app, attach the CV sent next."
        )
    if result.needs_gmail_reauth:
        note = (
            "🔌 *Gmail disconnected* — your token expired or scopes changed. "
            "Run /connect\\_gmail to restore one-tap drafts.\n\n" + note
        )
    if callback_id:
        telegram_api.answer_callback(
            callback_id,
            "Reconnect Gmail" if result.needs_gmail_reauth
            else "Apply link ready" if apply_url
            else "Add recipient" if missing_recipient
            else "Draft ready",
        )
    card_text = _match_message(job) + "\n\n" + note
    first_row = (
        [{"text": "✅ Apply directly", "url": apply_url}]
        if apply_url
        else [{"text": "📧 Compose in Gmail", "url": compose_url}]
    )
    reply_markup = {"inline_keyboard": [
        first_row,
        _outcome_row(job["id"]),
        [{"text": "🔗 Open listing", "url": job["url"]}],
    ]}
    if edit_message_id is not None:
        telegram_api.edit_message_text(
            chat_id, edit_message_id, card_text,
            reply_markup=reply_markup, parse_mode="Markdown",
        )
    else:
        telegram_api.send_message(
            chat_id, card_text, reply_markup=reply_markup, parse_mode="Markdown",
        )
    # 1. Ready-to-paste draft (To / Subject / Body) in a single code block.
    try:
        telegram_api.send_message(
            chat_id,
            _draft_template_text(result),
            parse_mode="Markdown",
        )
    except Exception:
        log.exception("draft send failed for user=%d job=%d", user["id"], job["id"])
    # 2. CV PDF as a document — recruiter-ready filename + caption nudge.
    if user["cv_pdf"]:
        try:
            telegram_api.send_document(
                chat_id,
                filename=user["cv_pdf_filename"] or "cv.pdf",
                content=bytes(user["cv_pdf"]),
                caption="📎 Attach this to the draft above.",
            )
        except Exception:
            log.exception("send_document failed for user=%d job=%d", user["id"], job["id"])
    else:
        telegram_api.send_message(
            chat_id,
            "⚠️ No CV stored — send a PDF first (then re-tap Apply).",
        )


# (emoji, headline) per manual outcome — used by _cb_outcome below.
_OUTCOMES: dict[str, tuple[str, str]] = {
    "interview": ("🎤", "Interview scheduled — good luck!"),
    "rejected": ("❌", "Marked as rejected."),
    "offer": ("🎉", "Offer received — congratulations!"),
}


def _cb_outcome(user: db.User, job: db.Job, cb: IncomingCallback) -> None:
    outcome = cb.data.partition(":")[0]
    emoji, headline = _OUTCOMES[outcome]
    if job["status"] == outcome:
        telegram_api.answer_callback(cb.callback_id, f"Already marked: {outcome}.")
        return
    db.update_job(job["id"], status=outcome)
    db.add_event(job["id"], user["id"], outcome)  # type: ignore[arg-type]
    analytics.track(user["id"], "outcome_marked", {"outcome": outcome})
    telegram_api.answer_callback(cb.callback_id, headline)
    # Keep the other outcome buttons — an interview can still become an
    # offer or a rejection later.
    remaining = [
        {"text": f"{e} {o.title()}", "callback_data": f"{o}:{job['id']}"}
        for o, (e, _) in _OUTCOMES.items()
        if o != outcome
    ]
    telegram_api.edit_message_text(
        cb.chat_id, cb.message_id,
        _match_message(job) + f"\n\n{emoji} *{headline}*",
        reply_markup={"inline_keyboard": [
            remaining,
            [{"text": "🔗 Open", "url": job["url"]}],
        ]},
        parse_mode="Markdown",
    )


def _cb_skip(user: db.User, job: db.Job, cb: IncomingCallback) -> None:
    db.update_job(job["id"], status="skipped")
    db.add_event(job["id"], user["id"], "skipped")
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
    db.add_event(job["id"], user["id"], "muted", {"company": company})
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
    "interview": _cb_outcome,
    "rejected": _cb_outcome,
    "offer": _cb_outcome,
}
