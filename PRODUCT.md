# JobFox — Product & Architecture

> Single source of truth for *what JobFox is, why it exists, where the
> code lives, and how it actually works.* Read this before touching code
> if you're new; skim the **Roadmap** section if you're returning.

---

## Table of contents

1. [TL;DR](#tldr)
2. [What it does](#what-it-does)
3. [Why it exists](#why-it-exists)
4. [End-to-end flow](#end-to-end-flow)
5. [Architecture](#architecture)
6. [Module map](#module-map)
7. [Data model](#data-model)
8. [The "Apply" pipeline (the moneymaker)](#the-apply-pipeline-the-moneymaker)
9. [Configuration](#configuration)
10. [Operations](#operations)
11. [Roadmap to paying customers](#roadmap-to-paying-customers)
12. [Monetization](#monetization)
13. [Risks](#risks)
14. [Contributing](#contributing)

---

## TL;DR

JobFox is an **AI job hunter — in Telegram, on the web, and (soon) on
mobile**. A user uploads a CV once,
the bot ingests jobs daily from LinkedIn / staff.am / Telegram channels,
scores fit with an LLM, writes a grounded cover letter, then — on a tap
— drops a **real Gmail draft** (with the CV attached) into the user's
own Gmail. The user reviews and sends from Gmail; nothing leaves their
account without their click.

**Stack:** Python 3.12 · FastAPI · Postgres · Gemini · Gmail API · Telegram
Bot API · deployed on Render.

**Differentiation in one line:** every competitor spray-and-prays from a
shared inbox and tanks deliverability. We send from the user's own
Gmail, reviewable, with a CV attached, grounded in the user's actual
experience. Quality over volume.

---

## What it does

From the user's seat:

1. Send the bot a **PDF résumé**. It extracts text (pdfplumber → OCR
   fallback) and asks an LLM to parse a structured profile (skills,
   experience, projects, summary).
2. Configure **search axes** with simple commands:
   `/queries`, `/locations`, `/channels`, `/worldwide`.
3. Run `/connect_gmail` once to grant `gmail.compose` scope. Now the
   "Apply" button creates **real Gmail drafts** instead of asking the
   user to copy-paste.
4. Either wait for the daily pipeline (cron) or `/run` it now.
5. For each match the bot pushes a card to Telegram with:
   - Title / company / location
   - Fit score 1–10 + one-sentence reason
   - The cover letter inline
   - Buttons: ✅ Apply · ⏭ Skip · 🔕 Mute · 🔗 Open
6. Tap **Apply** → draft appears in their Gmail, ready to review and
   send. Tap **Open Gmail drafts** → goes straight there, even if they
   have multiple Google accounts signed in.

All state (CV, profile, jobs, applies, OAuth tokens) lives in Postgres
per user, indexed by `tg_chat_id`.

---

## Why it exists

**Problem.** Job search is broken. Listings are everywhere, recruiters
get hundreds of generic CVs per role, candidates write one cover letter
and spray it. Auto-apply tools made this worse — bots fire spam from
shared inboxes that all land in `Promotions` or spam folders.

**Audience.** Mid-to-senior technical candidates who:
- want to apply intentionally to a curated few jobs a day, not 200,
- live in places (Armenia, Georgia, MENA, SE Asia) where local boards
  are sparse and global search is high-friction,
- can *write* a cover letter but don't want to write it 30 times.

**Why now.**
- LLMs make per-job cover letters cheap enough to ship in a hobby
  budget. A grounded 200-word letter costs <$0.001 on Gemini Flash.
- Gmail OAuth + draft API removes the deliverability ceiling that
  killed every spray-and-pray autoapply tool.
- Telegram is sticky, push by default, near-zero install friction —
  perfect for a "set it and forget it" agent.

**What's different.**
- **Reviewable drafts in the user's own Gmail** — no shared bot inbox,
  no Spam-folder risk, real sender reputation.
- **Grounded cover letters** — the LLM is locked to facts from the
  parsed profile. We refuse to invent employers or metrics.
- **Location-aware scoring** — a remote-or-Yerevan candidate isn't
  shown Bay Area onsite jobs scored 9/10.

---

## End-to-end flow

```
┌──────────┐  PDF       ┌────────────┐  text + profile  ┌──────────┐
│ Telegram │ ─────────► │  bot.py    │ ───────────────► │   db     │
│   user   │            │ (handler)  │                  │ (users)  │
└──────────┘            └─────┬──────┘                  └──────────┘
     ▲                        │
     │ /run or daily cron     ▼
     │                  ┌────────────┐  discover  ┌─────────────────┐
     │                  │ pipeline   │ ─────────► │ discovery.py    │
     │                  │   .py      │            │ (LI/staff/TG)   │
     │                  └─────┬──────┘            └────────┬────────┘
     │                        │                            │
     │                        ▼                            ▼
     │                  ┌────────────┐  score+cover  ┌─────────┐
     │                  │ match.py   │ ──────────►   │ Gemini  │
     │                  └─────┬──────┘               └─────────┘
     │  notify match          │
     │ ◄──────────────────────┘
     │
     │ tap ✅ Apply           ┌────────────┐  draft+attach  ┌──────────┐
     └──────────────────────► │ apply.py   │ ────────────►  │ Gmail API│
                              │            │  (or SMTP /     │ /Drafts │
                              │            │   deep_link)    └──────────┘
                              └────────────┘
```

**Lifecycle of a single job row:**

```
discovered ──score──► scored ──notify──► notified ──tap──► applied
   │                                                │
   │                                                ├──► skipped
   │                                                ├──► muted
   │                                                └──► failed
```

Every state transition is a row update + (where applicable) a `applies`
row insert. The status drives bot UI (Apply button is hidden once
status=applied).

---

## Architecture

**Single Python process** runs on Render. Three HTTP routes:

- `POST /telegram/webhook` — Telegram pushes every user update here.
  Acked instantly; work runs in a background thread so the webhook
  never times out and Telegram doesn't retry.
- `POST /cron` — daily orchestrator, secured by a shared secret. Fires
  the pipeline for every active user.
- `GET /oauth/google/callback` — completes the per-user Gmail OAuth
  consent flow.

**No external queue.** A background thread per webhook is enough at our
current scale; pipeline runs are long but rare. When we outgrow that
we'll move to RQ / Celery / SQS.

**State.** Postgres (Supabase pooler in prod). Schema migrations run on
startup via `db.run_migrations()`.

**LLM.** Gemini 2.5 Flash via the native REST API (not the OpenAI
shim). JSON mode for structured outputs (score, profile parse,
cv_tweaks). Plain text mode for cover letters.

---

## Module map

```
jobfox/
├── main.py             FastAPI app + 3 HTTP routes (webhook, cron, oauth)
├── config.py           Typed Settings (env-var-driven); single source of truth
├── db.py               Connection, migrations, User/Job/Apply CRUD
├── bot.py              Telegram commands + inline callbacks (Apply/Skip/Mute)
├── telegram_api.py     Thin HTTP wrapper for Telegram Bot API (sendMessage,
│                       editMessageText, sendDocument, …) — pure transport
├── discovery.py        Job source plugins: LinkedIn (JobSpy), staff.am, TG
├── profile.py          LLM-driven CV → structured profile (skills/exp/etc.)
├── match.py            Score, cover_letter, cv_tweaks; CV PDF→text extraction
├── llm.py              Gemini client (complete / complete_json); retry/backoff
├── apply.py            Three-tier apply transport: Gmail draft → SMTP → deep_link
├── gmail_api.py        OAuth (signed state, code exchange) + Drafts API
└── pipeline.py         Daily orchestrator: discover → score → cover → notify
```

**Why this split.** Each file is a single responsibility a single
contributor can own. Network calls are isolated in `*_api.py` modules
so business logic stays mockable. The pipeline is the only place that
knows the global order of operations.

---

## Data model

Three tables. All migrations live in `db.py::_MIGRATIONS` (append-only;
never edit an applied migration).

### `users`

| Column | Notes |
|---|---|
| `id` PK · `tg_chat_id` UNIQUE | Telegram chat is the identity |
| `email`, `name` | Used in cover letters + sign-off |
| `cv_text`, `cv_pdf`, `cv_pdf_filename` | Raw artifacts |
| `cv_profile` JSONB | LLM-parsed structured profile |
| `queries[]`, `locations[]`, `telegram_channels[]` | Search axes |
| `muted_companies[]` | One-tap mute persists across runs |
| `worldwide_ratio` 0–1 | Share of non-Armenia jobs to discover |
| `auto_apply` bool · `min_score_*` ints | Auto-apply threshold |
| `paused` bool | Pipeline skips this user |
| `gmail_refresh_token`, `gmail_address` | Per-user Gmail OAuth (mig 4) |

### `jobs`

| Column | Notes |
|---|---|
| `id` PK · `(user_id, url_hash)` UNIQUE | Dedupe per user, not global |
| `source` | `linkedin` / `staff_am` / `telegram` |
| `title`, `company`, `location`, `description`, `salary`, `recruiter_email` | |
| `score`, `reason` | LLM scoring output |
| `cover_letter` | LLM output, cached on the row |
| `cv_tweaks` JSONB | Optional CV nudges (unused in UI today) |
| `status` | new / scored / notified / applied / skipped / muted / failed |
| `notified_at`, `applied_at`, `apply_error` | |

### `applies`

One row per Apply tap. Append-only audit log of what we tried to send,
when, and where. `cv_pdf` is denormalized onto the row so we can
re-create a draft from history if needed.

| Column | Notes |
|---|---|
| `to_email`, `subject`, `body` | Snapshot at apply time |
| `cv_pdf` | Bytes (omitted for `deep_link` rows) |
| `status` | queued / sent / failed / deep_link |
| `sent_at`, `error` | |

---

## The "Apply" pipeline (the moneymaker)

Three transports, picked in priority order per call. See `apply.py`.

### 1. Gmail draft (preferred — when `/connect_gmail` ran)

**The wedge feature.** We construct an RFC-2822 MIME message
(`EmailMessage.add_attachment` handles the multipart wrap, base64
encoding, and Content-Disposition for the CV PDF), base64url-encode the
bytes, and POST it to `gmail.users.drafts.create`.

Result: a real draft sits in the user's *own* Drafts folder with their
real From, real signature, the CV attached, ready to review and send.

**Why drafts and not direct send:**
- One-click review-then-send in the user's own Gmail UI.
- No restricted-scope review needed (auto-send requires `gmail.send`
  which is restricted; `gmail.compose` is sensitive but achievable).
- Zero "did the email actually leave?" anxiety on our side. If the
  draft exists in their Gmail, the job is done.

**OAuth flow** (see `gmail_api.py`):
- `/connect_gmail` builds an authorization URL with a signed state
  token (HMAC-SHA256 of `chat_id|timestamp` with the pipeline secret;
  10-min TTL). Sent as an **inline-keyboard button** (NEVER inline in
  Markdown — underscores in `access_type`, `response_type` etc. trigger
  Telegram's italic parser and silently chop the URL).
- User taps → consents → Google redirects browser to
  `/oauth/google/callback` with `?code=…&state=…`.
- We validate the state's HMAC + age, exchange the code for a
  `refresh_token` (+ user's Gmail address via the `userinfo.email`
  scope), persist on the user row, ping them in Telegram.
- Refresh tokens are long-lived; access tokens are minted on demand
  inside `_credentials()` and never stored.

**Cover letter generation** (`match.py::cover_letter`):
Tight prompt — 140–200 words, 3–4 short paragraphs, no superlatives, no
filler. *Grounded:* the LLM only sees the structured profile and a
truncated raw CV, with a hard rule against inventing employers or
metrics. The bot adds a salutation (`Hi <Company> team,`) and signature
(`Best, <Name> <Email>`) so wording stays consistent across
applications — the LLM doesn't get to re-roll the bookends.

### 2. SMTP (legacy single-bot-inbox fallback)

Used when Gmail OAuth isn't connected AND `GMAIL_ADDRESS` +
`GMAIL_APP_PASSWORD` are set AND we have a recruiter email. Sends via
Gmail SMTP over SSL with Reply-To set to the candidate's email. Lower
deliverability than path 1, kept for users who don't want to grant
OAuth.

### 3. Deep-link (fallback)

When neither transport works (no Gmail OAuth, no SMTP creds, no
recruiter email): we hand the user a copy-paste draft in a single
Telegram code block (long-press → Copy on mobile, paste into any email
app) plus the CV as a separate `sendDocument` attachment.

---

## Configuration

All config flows through `jobfox/config.py::Settings`. Env vars are
required unless noted.

| Var | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | ✅ | Postgres (Supabase pooler in prod) |
| `GEMINI_API_KEY` | ✅ | LLM |
| `GEMINI_MODEL` | optional | Defaults to `gemini-2.5-flash` |
| `TELEGRAM_BOT_TOKEN` | ✅ | Bot identity |
| `TELEGRAM_WEBHOOK_SECRET` | optional | Verify Telegram's `X-Telegram-Bot-Api-Secret-Token` header |
| `PIPELINE_SECRET` | ✅ | Auth for `/cron` AND the HMAC key for OAuth state tokens |
| `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` | optional | Legacy SMTP transport |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | optional | Gmail OAuth (drafts) |
| `APP_URL` | optional | Public origin used for OAuth redirect (e.g. `https://jobfox-api.onrender.com`). **Origin only — code appends `/oauth/google/callback`.** |
| `WORLDWIDE_RATIO_DEFAULT` | optional | Default for new users (0.1) |
| `MIN_SCORE_NOTIFY_DEFAULT` | optional | 6 |
| `MIN_SCORE_AUTO_APPLY_DEFAULT` | optional | 8 |

**OAuth setup checklist** (Google Cloud Console):
1. **OAuth Overview → Get started** → fill in app name, email, External
   audience.
2. **Audience → Test users** → add every Gmail address that will
   connect while the app is in Testing (otherwise consent screen
   blocks). Move to Production after passing verification.
3. **APIs & Services → Library** → enable **Gmail API**.
4. **Clients → + Create client** → Web application →
   Authorized redirect URIs = `<APP_URL>/oauth/google/callback`
   (byte-exact match required).
5. Copy Client ID + Secret into env. Restart app. Run `/connect_gmail`
   in Telegram.

---

## Operations

### Deployment (Render)

```
render.yaml → webservice on Render
  python -m uvicorn jobfox.main:app --host 0.0.0.0 --port $PORT
```

- Startup runs `db.run_migrations()` — append-only `_MIGRATIONS` list
  in `db.py`. Safe to redeploy; already-applied versions skip.
- Telegram webhook is set once via the Telegram setWebhook API to
  `https://<app>/telegram/webhook` with the
  `X-Telegram-Bot-Api-Secret-Token` header.
- The daily pipeline runs via **GitHub Actions** (or any cron) hitting
  `POST /cron` with `X-Pipeline-Secret`.

### Migrations

```python
# db.py
_MIGRATIONS = [
    (1, "<initial schema>"),
    (2, "ALTER TABLE users ADD COLUMN name TEXT;"),
    (3, "ALTER TABLE users ADD COLUMN cv_profile JSONB;"),
    (4, "<gmail_refresh_token + gmail_address>"),
]
```

**Rule:** append, never edit. The migration runner records applied
versions in `schema_migrations` and only runs new ones.

### Observability today

- **Logs** — Python `logging` to stdout, captured by Render.
- **Sentry / PostHog** — *not wired yet.* Top of the Phase 1 list.
- **Internal admin** — none. `psql` to the database is the current
  "admin panel."

### Cost model (per active user / month)

| Item | Cost |
|---|---|
| Gemini scoring (~150 jobs/mo × ~$0.0001) | ~$0.015 |
| Gemini cover letters (~30 apply taps × ~$0.0005) | ~$0.015 |
| Render web service (shared across users) | ~$7/mo flat |
| Supabase (free tier holds ~hundreds of users) | $0 |
| Telegram, Gmail API | $0 |

**Marginal LLM cost is essentially zero** until we hit Gemini's free
tier ceiling, which lets us run Free tier at break-even for a long
time.

---

## Roadmap to paying customers

Where we are vs. real-world-deployable, in honest terms.

### ✅ Done

- Telegram bot UX with all the core commands, plus a web app (landing,
  login, dashboard, account)
- PDF parsing + structured profile extraction
- Multi-source discovery (LinkedIn, staff.am, Telegram channels)
- LLM scoring with location awareness
- Grounded cover letter generation
- Three-tier apply transport (Gmail draft / SMTP / deep_link)
- Gmail OAuth (signed state, refresh token storage, drafts API)
- Migrations, retries, basic error handling
- 41 unit tests

### ❌ Blocks first paying customer

- No web signup / no billing surface
- No quotas — one user with `/run` in a loop could burn through Gemini
  free tier
- No analytics — can't measure funnel
- No ToS / Privacy Policy — required for Google OAuth verification
- Google OAuth app still in **Testing** mode (consent screen warns
  "This app isn't verified"; only listed test users can connect)
- No reply tracking — apply status stops at `applied`, never `replied`
  / `interview`
- LinkedIn discovery is fragile (TOS + IP blocks)

### Phase 1 — first paying customer (4–6 weeks)

Each item ships independently; tag the deploy when done.

1. **User tier + weekly apply quota** (1 day) — `user.tier` column,
   `applies_this_week` counter, weekly reset cron, `/apply` blocks
   above the limit with an upgrade CTA. Lets you cap free usage today.
2. **Stripe Checkout + webhook → tier='pro'** (2 days) — actual
   monetization.
3. **Landing page** (1 day) — separate subdomain, pricing, signup
   waitlist email capture even before payment is live.
4. **Sentry + PostHog wiring** (0.5 day) — every product decision after
   this point needs data.
5. **ToS + Privacy Policy + data export/delete endpoints** (1 day) —
   required by Google verification, required by users for trust.
6. **Submit Google OAuth verification** (2–4 weeks of back-and-forth
   wall-clock; ~1 day of work) — removes the "unverified app" screen.

### Phase 2 — PMF experiments (8–12 weeks)

Each is a 1–2 week experiment; kill or scale based on metrics.

| Experiment | Why it matters |
|---|---|
| **Greenhouse / Lever / Workday / Ashby ATS discovery** | Replaces fragile LinkedIn scraping with stable structured feeds |
| **Reply tracking** via Gmail history API (extra scope) | Auto-flip status to `replied`; dashboard becomes valuable on its own |
| **Interview prep** generator | Pro-tier feature; near-zero marginal cost |
| **Salary intelligence** per match | Conversion driver |
| **CV variants** (LLM picks per role family) | Justifies Power tier |
| **LinkedIn Easy Apply automation** (browser extension) | The feature that justifies $49/mo Power tier; ToS risk acknowledged |

### Phase 3 — scale & moat (post-PMF)

- B2B partnerships with bootcamps / career services (white-label at
  $5–10k/year per institution).
- API tier for career coaches.
- Localization for adjacent markets (Georgia, Ukraine, MENA, SE Asia)
  using the same Armenia-style local-bias scoring.
- Human-coach add-on tier ($149/mo) with revenue-share to external
  coaches.

---

## Monetization

### Recommended pricing

| Tier | Price | Limits | Hook |
|---|---|---|---|
| **Free** | $0 | 5 applies/week, 3 saved queries, basic matching | Habit formation |
| **Pro** | **$19/mo** | 50 applies/week, all sources, interview prep, salary insights | Sweet spot; converts 5–10% of active free |
| **Power** | **$49/mo** | 200/week, LinkedIn Easy-Apply, multiple CV variants, ATS optimizer | Power users & career transitions |
| **Coach** | **$149/mo** | Human review of first 5 apps + weekly 30-min call (outsourced) | High-margin add-on |

**LTV math (rough):**
- Avg job search ≈ 8–12 weeks.
- Pro at $19 × 2.5 months = **$47 LTV**.
- Power at $49 × 2.5 = **$122 LTV**.
- At $5 CAC (Reddit, indie hackers, organic Telegram), 9–24× ROAS once
  conversion exists at all.

### The retention problem

Users get a job and churn forever. Mitigate with:
- **"Career insurance"** $99/year annual plan — low-volume monitoring
  continues so you're already there when they want to leave their next
  job 18 months out.
- **Coach add-on** at $149 builds an ongoing relationship moat.
- **Referral fees** from bootcamps / coding schools / resume writers —
  once you have CV + interest signals, qualified leads are valuable.

### Why not pay-per-apply credits

Higher churn, harder for users to budget, kills the "let it run in the
background" UX that's the whole point.

---

## Risks

Ranked by what kills the business fastest.

| Risk | What kills you | Mitigation |
|---|---|---|
| **Google revokes our OAuth app** | All Gmail drafts stop → 100% churn overnight | Pass verification early; stay on `gmail.compose` only (never `gmail.send`, which is restricted scope); behave during the review |
| **LinkedIn blocks or sues over scraping** | Lose a major job source | Migrate core discovery to ATS APIs (Greenhouse/Lever/Workday/Ashby); treat LinkedIn as bonus, not core |
| **LLM cost runaway** | Margin collapse | Per-user daily token budget; cache scoring per `(job_hash, cv_hash)`; use cheaper model for scoring vs. cover letter |
| **Cover letter quality drift** | Refunds, reputation damage | A/B prompts; thumbs-up/down per draft; auto-regenerate when ratio drops |
| **Data breach** | Game over (CVs + emails + Gmail refresh tokens) | Encrypt refresh tokens at rest; rotate `PIPELINE_SECRET` on a schedule; SOC 2-lite checklist before B2B |
| **Telegram bot ban** | Entire UX gone | Keep the web dashboard as a fallback channel; daily email digest as a third leg |

---

## Contributing

### Local dev

```bash
# clone, then
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.test .env       # fill in real creds
.venv/bin/uvicorn jobfox.main:app --reload --port 8000
```

For the Telegram side without a public URL, use [ngrok] or run
`scripts/run_pipeline.py` directly against your dev database.

[ngrok]: https://ngrok.com/

### Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

Conventions:
- One file = one responsibility. New external API? New `*_api.py`
  module.
- LLM prompts live where they're called — don't centralize them
  prematurely.
- Migrations append-only.
- No silent failure: every transport degrades to the next tier with a
  log line, never swallows exceptions.
- Tests for security-sensitive code (auth tokens, signed state, API
  keys) are non-negotiable — see `tests/test_gmail_state.py` for the
  pattern.

### Conventional commits

`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`. The body should
explain *why*; the diff already shows *what*.

---

*Last updated: when you last edited this file. Keep it honest.*
