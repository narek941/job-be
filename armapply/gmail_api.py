"""Gmail Drafts API integration.

Per-user OAuth (refresh-token long-lived; access tokens minted on demand)
+ a single `create_draft` call that drops a real Gmail draft — To, Subject,
Body, and the CV PDF attached — into the candidate's own Drafts folder.

Why drafts (not direct send):
  * One-click review-then-send in the user's own Gmail UI on any device.
  * The candidate's natural From: + signature + tracking — no
    `bot@example.com` lookalike to land in Spam.
  * Zero "did the email actually leave?" anxiety on our side; if Google
    accepts the draft, it exists in the user's account.

Scope is `gmail.compose` only — we can't read mail, only create drafts
and send them. Refresh tokens persist across our restarts; access tokens
are minted per call inside the Google client and never stored.

OAuth state-token scheme (anti-CSRF):
  `{chat_id}.{unix_ts}.{hex_hmac}` signed with pipeline_secret. Anyone
  hitting /oauth/google/callback with a forged state can't bind a Gmail
  account to a chat they don't own, and the 10-minute TTL stops replay.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import mimetypes
import time
from email.message import EmailMessage
from typing import Literal
from urllib.parse import quote, urlencode

import httpx

from armapply.config import settings

# google-auth + googleapiclient are imported lazily inside _credentials /
# create_draft so the module loads even on a fresh checkout where the
# Google deps haven't been pip-installed yet (e.g. unit tests that don't
# touch the draft path). Only callers of create_draft / exchange_code
# actually need them.

log = logging.getLogger(__name__)


# `gmail.compose` is the narrowest scope that lets us create drafts AND
# send them. `gmail.send` alone can't create drafts; `gmail.modify` is
# wider than we need. `userinfo.email` is so we can show the user which
# Gmail account they connected (and pick the right `/u/<n>/` URL).
SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]

_OAUTH_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

_STATE_TTL_SECONDS = 10 * 60
_HTTP_TIMEOUT = httpx.Timeout(20.0, connect=10.0)


class GmailNotConnected(RuntimeError):
    """User hasn't run /connect_gmail (no refresh token stored)."""


class GmailDraftError(RuntimeError):
    """Wraps any Google API failure during draft creation."""


class GmailReauthRequired(GmailDraftError):
    """Refresh token is no longer valid — user must run /connect_gmail again.

    Raised on `invalid_grant` (revoked/expired) and `invalid_scope` (granted
    scope set no longer matches what we ask for). The bot surfaces this so
    the user knows to reconnect rather than just seeing a generic fallback."""


# ---------------------------------------------------------------------------
# OAuth state — signed, time-bounded
# ---------------------------------------------------------------------------

def _sign(payload: str) -> str:
    key = settings().pipeline_secret.encode()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def make_state(chat_id: int) -> str:
    """Returns a `{chat_id}.{ts}.{hmac}` token to embed in the OAuth URL."""
    ts = int(time.time())
    payload = f"{chat_id}.{ts}"
    return f"{payload}.{_sign(payload)}"


def parse_state(state: str) -> int:
    """Validates the state and returns the chat_id. Raises ValueError on
    tamper, expiry, or malformed input. NEVER trust an unvalidated state."""
    parts = state.split(".")
    if len(parts) != 3:
        raise ValueError("malformed state")
    chat_id_str, ts_str, sig = parts
    payload = f"{chat_id_str}.{ts_str}"
    if not hmac.compare_digest(_sign(payload), sig):
        raise ValueError("bad signature")
    try:
        ts = int(ts_str)
        chat_id = int(chat_id_str)
    except ValueError as e:
        raise ValueError("bad numeric fields") from e
    if time.time() - ts > _STATE_TTL_SECONDS:
        raise ValueError("expired")
    return chat_id


# ---------------------------------------------------------------------------
# OAuth URL + code exchange
# ---------------------------------------------------------------------------

def make_oauth_url(chat_id: int) -> str:
    """Build the consent-screen URL the user clicks from Telegram.

    Requesting `access_type=offline` + `prompt=consent` is what makes
    Google return a refresh_token. Without `prompt=consent`, a returning
    user who already granted scopes is redirected back with NO refresh
    token — and we have nothing to persist.

    Pre-flight validation: catch a misconfigured `APP_URL` here so the
    user gets a clear Telegram error instead of Google's opaque
    'Error 400: invalid_request' with no parameter named.
    """
    s = settings()
    if not s.gmail_oauth_configured:
        raise GmailNotConnected("Gmail OAuth env vars are missing.")
    redirect_uri = s.gmail_redirect_uri
    if not redirect_uri.startswith(("http://", "https://")):
        raise GmailNotConnected(
            f"APP_URL must include a scheme (got {s.app_url!r}). "
            "Set APP_URL=https://your-domain.com (or http://localhost:8000 "
            "for local dev), then redeploy."
        )
    if not s.google_client_id.endswith(".apps.googleusercontent.com"):
        raise GmailNotConnected(
            "GOOGLE_CLIENT_ID looks malformed — expected a value ending in "
            "`.apps.googleusercontent.com`. Re-copy it from Cloud Console "
            "→ APIs & Services → Credentials → your OAuth client."
        )
    from urllib.parse import urlencode

    params = {
        "client_id": s.google_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": make_state(chat_id),
    }
    url = f"{_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"
    # Log the redirect_uri (NOT the full URL — state token bloats it) so
    # ops can grep for what we sent vs. what's registered in the Console.
    log.info("OAuth start chat=%s redirect_uri=%s client_suffix=%s",
             chat_id, redirect_uri, s.google_client_id[-12:])
    return url


def exchange_code(code: str) -> tuple[str, str]:
    """Trade an auth code for (refresh_token, gmail_address).

    Returns the address from Google's userinfo endpoint so we can show the
    user which account they actually granted access to — they might have
    multiple Google accounts signed in and picked the wrong one.
    """
    s = settings()
    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        r = client.post(
            _OAUTH_TOKEN_URL,
            data={
                "code": code,
                "client_id": s.google_client_id,
                "client_secret": s.google_client_secret,
                "redirect_uri": s.gmail_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    if r.status_code != 200:
        raise GmailDraftError(f"token exchange failed: HTTP {r.status_code} {r.text[:300]}")
    tok = r.json()
    refresh_token = tok.get("refresh_token")
    access_token = tok.get("access_token")
    if not refresh_token:
        # See the prompt='consent' note in make_oauth_url — if we get
        # here it usually means the user previously granted the same
        # scopes and Google decided to re-use the prior grant without
        # issuing a new refresh token.
        raise GmailDraftError(
            "Google didn't return a refresh_token. "
            "Revoke our access at https://myaccount.google.com/permissions "
            "and try /connect_gmail again."
        )
    if not access_token:
        raise GmailDraftError("Google didn't return an access_token.")

    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        ur = client.get(
            _USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if ur.status_code != 200:
        raise GmailDraftError(f"userinfo failed: HTTP {ur.status_code} {ur.text[:300]}")
    email = ur.json().get("email", "")
    if not email:
        raise GmailDraftError("userinfo response missing 'email'.")
    return refresh_token, email


# ---------------------------------------------------------------------------
# Draft creation
# ---------------------------------------------------------------------------

def _credentials(refresh_token: str):  # type: ignore[no-untyped-def]
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from google.oauth2.credentials import Credentials

    s = settings()
    # Intentionally omit `scopes=` — when set, google-auth forwards them as
    # the `scope` param on refresh, and Google returns `invalid_scope` if the
    # current SCOPES list isn't a subset of what the refresh token was
    # originally granted (e.g. user connected before we added `openid`).
    # Without it, refresh succeeds against whatever was granted; we only
    # use `gmail.compose` from that grant, which is the narrowest scope we ask
    # for, so a smaller granted set still works.
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=_OAUTH_TOKEN_URL,
        client_id=s.google_client_id,
        client_secret=s.google_client_secret,
    )
    try:
        creds.refresh(GoogleAuthRequest())
    except RefreshError as e:
        # `invalid_grant` = revoked/expired; `invalid_scope` = scope mismatch
        # that omitting scopes above didn't catch (rare, e.g. a scope was
        # disabled in GCP). Either way the user has to reconnect.
        raise GmailReauthRequired(f"Gmail token refresh failed: {e}") from e
    return creds


def _build_mime(
    *,
    to: str,
    from_addr: str,
    subject: str,
    body: str,
    cv_pdf: bytes | None,
    cv_filename: str | None,
) -> bytes:
    """Assemble an RFC 2822 MIME message with the CV attached. Returns the
    raw bytes ready to be base64url-encoded for `users.drafts.create`.

    `EmailMessage.add_attachment` handles the multipart wrap, Content-Type,
    Content-Disposition, base64 encoding, and Content-Transfer-Encoding —
    so we don't hand-roll any of it."""
    msg = EmailMessage()
    if to:
        msg["To"] = to
    msg["From"] = from_addr
    msg["Subject"] = subject
    msg.set_content(body)

    if cv_pdf:
        filename = cv_filename or "cv.pdf"
        maintype, subtype = "application", "pdf"
        guessed, _ = mimetypes.guess_type(filename)
        if guessed and "/" in guessed:
            maintype, subtype = guessed.split("/", 1)
        msg.add_attachment(cv_pdf, maintype=maintype, subtype=subtype, filename=filename)

    return msg.as_bytes()


def create_draft(
    *,
    refresh_token: str,
    from_addr: str,
    to: str | None,
    subject: str,
    body: str,
    cv_pdf: bytes | None,
    cv_filename: str | None,
) -> str:
    """Create a draft in the user's Gmail and return the draft id.

    `to` may be None — Gmail accepts a draft without a To: header so the
    user can fill it in before sending (this matches our deep_link path
    where no recruiter email is known)."""
    if not refresh_token:
        raise GmailNotConnected("missing refresh_token")

    creds = _credentials(refresh_token)
    raw = _build_mime(
        to=to or "",
        from_addr=from_addr,
        subject=subject,
        body=body,
        cv_pdf=cv_pdf,
        cv_filename=cv_filename,
    )
    encoded = base64.urlsafe_b64encode(raw).decode()

    try:
        from googleapiclient.discovery import build

        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        draft = (
            service.users()
            .drafts()
            .create(userId="me", body={"message": {"raw": encoded}})
            .execute()
        )
    except Exception as e:  # HttpError, transport, auth refresh, etc.
        raise GmailDraftError(f"Gmail draft failed: {e}") from e

    draft_id = str(draft.get("id") or "")
    if not draft_id:
        raise GmailDraftError(f"Gmail returned no draft id: {draft!r}")
    log.info("Gmail draft created: id=%s to=%s subject=%r", draft_id, to, subject)
    return draft_id


def _mail_account_path(gmail_address: str | None) -> str:
    if gmail_address:
        return f"/mail/u/{gmail_address}"
    return "/mail/u/0"


def web_compose_url(*, to: str | None, subject: str, body: str) -> str:
    """Desktop/mobile-web Gmail compose URL with subject + body pre-filled."""
    params: dict[str, str] = {
        "view": "cm",
        "fs": "1",
        "su": subject[:200],
        "body": body[:6000],
    }
    if to:
        params["to"] = to
    return "https://mail.google.com/mail/?" + urlencode(params)


def _app_compose_params(*, to: str | None, subject: str, body: str) -> dict[str, str]:
    params: dict[str, str] = {
        "subject": subject[:200],
        "body": body[:6000],
    }
    if to:
        params["to"] = to
    return params


def app_compose_url(*, to: str | None, subject: str, body: str) -> str:
    """Native Gmail compose deep link (iOS and fallback)."""
    return "googlegmail:///co?" + urlencode(_app_compose_params(to=to, subject=subject, body=body))


def app_compose_intent(*, to: str | None, subject: str, body: str, fallback_web: str) -> str:
    """Android intent URI that opens the Gmail app compose screen."""
    qs = urlencode(_app_compose_params(to=to, subject=subject, body=body))
    fallback = quote(fallback_web, safe="")
    return (
        f"intent://co?{qs}#Intent;scheme=googlegmail;"
        f"package=com.google.android.gm;S.browser_fallback_url={fallback};end"
    )


def app_gmail_url() -> str:
    """Open the native Gmail app (inbox)."""
    return "googlegmail:///"


def app_gmail_intent(*, fallback_web: str) -> str:
    """Android intent URI that opens the native Gmail app."""
    fallback = quote(fallback_web, safe="")
    return (
        "intent://#Intent;scheme=googlegmail;"
        f"package=com.google.android.gm;S.browser_fallback_url={fallback};end"
    )


def drafts_url(gmail_address: str | None, *, draft_id: str | None = None) -> str:
    """Web URL to the user's Gmail Drafts folder.

    `/u/<email>/` makes Gmail pick the right account when the user is
    multi-signed-in. When `draft_id` is set, Gmail opens that draft
    directly (`#drafts?compose=<id>`)."""
    base = f"https://mail.google.com{_mail_account_path(gmail_address)}/#drafts"
    if draft_id:
        return f"{base}?compose={draft_id}"
    return base


def gmail_link_url(
    *,
    kind: Literal["compose", "drafts"],
    to: str | None = None,
    subject: str = "",
    body: str = "",
    gmail_address: str | None = None,
    draft_id: str | None = None,
) -> str:
    """URL for Telegram inline buttons.

    When APP_URL is configured, returns an https redirect on our server
    that opens the native Gmail app on mobile. Otherwise returns the
    mail.google.com URL directly (local dev).
    """
    if kind == "drafts":
        web = drafts_url(gmail_address, draft_id=draft_id)
    else:
        web = web_compose_url(to=to, subject=subject, body=body)

    app_url = settings().app_url.rstrip("/")
    if not app_url:
        return web

    q: dict[str, str] = {}
    if kind == "compose":
        if to:
            q["to"] = to
        if subject:
            q["subject"] = subject[:200]
        if body:
            q["body"] = body[:6000]
    else:
        if gmail_address:
            q["account"] = gmail_address
        if draft_id:
            q["draft"] = draft_id
    return f"{app_url}/gmail/{kind}?{urlencode(q)}"


def gmail_redirect_html(
    web_url: str,
    *,
    ios_url: str | None = None,
    android_url: str | None = None,
    prefer_web: bool = False,
) -> str:
    """Minimal HTML page that opens the Gmail app on mobile, web elsewhere."""
    web_js = json.dumps(web_url)
    ios_js = json.dumps(ios_url or "")
    android_js = json.dumps(android_url or "")
    prefer_js = "true" if prefer_web else "false"
    web_attr = quote(web_url, safe="")
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Open Gmail</title>
<style>body{{font:16px system-ui;margin:48px auto;max-width:420px;color:#222;text-align:center}}
a{{color:#1a73e8}}</style>
<script>
(function(){{
  var web = {web_js};
  var ios = {ios_js};
  var android = {android_js};
  var preferWeb = {prefer_js};
  var ua = navigator.userAgent || "";
  var mobile = /Android|iPhone|iPad|iPod/i.test(ua);
  if (!mobile) {{ location.replace(web); return; }}
  if (preferWeb) {{ location.replace(web); return; }}
  var app = /Android/i.test(ua) ? android : ios;
  if (app) {{
    location.replace(app);
    setTimeout(function() {{ location.replace(web); }}, 1200);
  }} else {{
    location.replace(web);
  }}
}})();
</script>
<noscript><meta http-equiv="refresh" content="0;url={web_attr}"></noscript>
</head><body>
<p>Opening Gmail…</p>
<p><a href="{web_url}">Tap here if nothing happens</a></p>
</body></html>"""
