# JobFox — Full Project Plan

*Updated 2026-06-11. Companion to [PRODUCT.md](./PRODUCT.md) (architecture,
cost model). This is the execution plan: product vision, business, and
development, sequenced into sprints.*

---

## 1. Product vision — the end-to-end journey

JobFox is an **AI job hunter covering the whole path from human to
employee**, on every surface: **web and Telegram today, mobile app next**,
in **three languages (EN / HY / RU)**. The user configures everything once
and the product carries them through:

```
DISCOVER          ONBOARD              MATCH              APPLY
landing page  →   register (Telegram → daily discovery → one-tap or
(web / TG)        login is enough),    AI fit scoring,    auto-apply,
                  fill profile: CV,    notifications      reaches a real
                  role wanted, salary,                    recruiter inbox
                  locations, language
                                                              ↓
   HIRED          OFFER               INTERVIEW           TRACK
celebrate,    ←   offer analytics, ←  calendar with    ←  automatic status
career-           negotiation         dates, expected     checking (Gmail
insurance         context             questions per job,  replies → AI
mode                                  market salary,      classified),
                                      company intel,      full analytics
                                      mock interview
```

### Journey stages → features → plan gating

| Stage | Feature | Status | Free | Pro | Power |
|---|---|---|---|---|---|
| Onboard | Telegram-login register (web + TG) | ✅ live | ✓ | ✓ | ✓ |
| Onboard | Profile: CV upload, desired role, **salary expectations**, locations, language | CV via bot only; salary/role fields missing | ✓ | ✓ | ✓ |
| Onboard | Portfolio links (GitHub, Behance, site) merged into applications | ❌ | — | ✓ | ✓ |
| Match | Daily multi-source discovery + AI fit scoring | ✅ live | ✓ | ✓ | ✓ |
| Match | Notifications (TG now; web push + email later) | TG only | ✓ | ✓ | ✓ |
| Apply | One-tap apply, Gmail draft with CV | ✅ live | 5/wk | 50/wk | 200/wk |
| Apply | **Auto-apply** above threshold | ✅ live | — | ✓ | ✓ |
| Track | Manual outcome buttons + /stats | ✅ live | ✓ | ✓ | ✓ |
| Track | **Automatic status checking** (reply detection + AI classification) | ❌ S3 | — | ✓ | ✓ |
| Track | Full analytics dashboard (funnel, rates, per-job timeline) | basic | basic | full | full |
| Interview | **Calendar**: interview dates per job, reminders | ❌ S5 | — | ✓ | ✓ |
| Interview | **Expected questions** for this job/company | ❌ S5 | — | ✓ | ✓ |
| Interview | **Market salary** for this role/region | ❌ S5 | — | ✓ | ✓ |
| Interview | **Company intel** (site, LinkedIn presence, size, reviews) | ❌ S5 | — | teaser | ✓ |
| Interview | **Mock interview / prep session** (LLM interviewer) | ❌ S6 | — | — | ✓ |
| Offer | Offer-vs-market analysis, negotiation pointers | ❌ S6 | — | — | ✓ |
| All | 3 languages: EN / HY / RU (UI + LLM outputs) | EN only | ✓ | ✓ | ✓ |
| All | Web + Telegram parity, then mobile app | partial | ✓ | ✓ | ✓ |

This matrix *is* the pricing page: Free proves matching works, Pro
automates the grind (volume + tracking + interview support), Power adds
the career-coach layer.

### LLM economy — cheap by design, quality where it shows

Principle: **spend tokens where the user sees them, cache everything
else.**

| Workload | Model | Cost control |
|---|---|---|
| Fit scoring (high volume) | Gemini Flash | cache per `(job_hash, cv_hash)` — a job is scored once per CV ever; pre-filter by title heuristics so obvious misses never reach the LLM (already live) |
| Reply classification | Gemini Flash | only on new replies; ~zero volume per user |
| Cover letters (user-visible) | Flash now; A/B better model on Pro | generated only on Apply tap, never in bulk |
| Expected questions / salary / company intel | Flash + caching **per job, not per user** | two users matching the same job share one generation |
| Mock interview (Power) | best available | gated to the $49 tier, usage-capped |
| Everything | per-user daily token budget | hard stop + upsell message |

Multi-language costs ~nothing extra: same prompt, output language set by
user preference.

---

## 2. Critical priorities (P0 — everything else waits)

### P0-A · Recruiter email extraction — applies must reach humans

Coverage today is ~0%; every downstream number (replies, interviews,
testimonials, conversion) is built on this.

1. **staff.am `hr_mail`** — detail pages embed
   `"hr_mail":"<job>@e.staff.am"` in a JSON blob the text-only extractor
   misses. ~0% → ~100% on the #1 source. *(Verified live 2026-06-11.)*
2. **Everywhere else** — `mailto:` hrefs (incl. job.am RSS before
   tag-stripping, Telegram post HTML), de-obfuscation (`hr [at] x [dot]
   am`), all-candidate ranking (prefer hr@/careers@/cv@, drop noreply@ and
   image-name false positives).
3. **Measure** — per-source extraction rate logged to `pipeline_runs`;
   first KPI on the ops dashboard; alarms if a source's rate collapses.

### P0-B · ToS, Privacy Policy, data export/delete — legal to operate

Gates Google OAuth verification (longest external dependency), Stripe,
and any marketing push.

1. ToS + Privacy pages on the web app, linked from bot /start.
2. `GET /api/me/export` — full JSON dump of the user's data.
3. `DELETE /api/me` + `/delete_me` bot command — hard delete incl. Gmail
   token revocation.
4. Submit Google OAuth verification the same week, with `gmail.readonly`
   already in scope (no second review for status tracking later).

---

## 3. Sprint plan

**v1.0 (Sprints 1–4):** a stranger can sign up on web or Telegram, build
a full profile, get matches whose applies reach recruiters, pay, and see
the funnel fill automatically.
**v1.5 (Sprints 5–6):** the interview-copilot layer + 3 languages.
**v2.0 (Sprints 7–8):** mobile app + offer/negotiation layer.

2-week sprints, solo-dev pace, every task ≤4 days.

### Sprint 1 (Wk 1–2) — "Applies reach humans, legally" → M0

| ID | Task | Size | Acceptance |
|---|---|---|---|
| S1-1 🔴 | ~~staff.am `hr_mail` extraction~~ ✅ 2026-06-12 | 0.5 d | **verified live: 8/8 (100%)** — also fixed broken description scraping (page went JS-rendered; now parsed from the same JSON blob) |
| S1-2 🔴 | ~~Email extraction v2: mailto, ranking, de-obfuscation, false-positive filter~~ ✅ 2026-06-12 | 1.5 d | per-pattern unit tests pass; verified TG/job.am posts genuinely contain no emails (extraction works; content links to staff.am, covered by S1-1) |
| S1-3 🔴 | ~~Per-source extraction-rate logging + alarm~~ ✅ 2026-06-12 | 0.5 d | rates logged to `pipeline_runs` per run; staff.am <50% rate → ERROR alarm |
| S1-4 🔴 | ~~ToS + Privacy pages, linked everywhere~~ ✅ 2026-06-12 | 1 d | `/terms` + `/privacy` render (verified in preview); linked from footer, login, bot help. ⚠️ set the real support email in `Legal.tsx` before OAuth submission |
| S1-5 🔴 | ~~Data export + delete (API + bot + token revoke)~~ ✅ 2026-06-12 | 1 d | `GET /api/me/export` (secrets excluded), `DELETE /api/me` + `/delete_me confirm`, Gmail revoke; tested |
| S1-6 🔴 | Submit Google OAuth verification (incl. `gmail.readonly`) — **checklist ready: [docs/oauth-verification.md](docs/oauth-verification.md); Console submission is a manual owner step (needs prod domain + support email first)** | 0.5 d | clock started |
| S1-7 | ~~Copy pass: "AI job hunter — Telegram · web · mobile soon" (landing, README, PRODUCT.md)~~ ✅ 2026-06-12 | 0.5 d | no "Telegram-first" left in user-facing copy |
| S1-8 | ~~Sentry + PostHog (be + fe + bot)~~ ✅ 2026-06-12 | 0.5 d | config-gated (`SENTRY_DSN`, `POSTHOG_API_KEY`, `VITE_POSTHOG_KEY`); events: signup, cv_uploaded, apply, outcome_marked, web_login |

### Sprint 2 (Wk 3–4) — "Full profile, sellable on every surface" → M1

| ID | Task | Size | Acceptance |
|---|---|---|---|
| S2-1 | ~~**Profile v2: desired role/title, salary expectations (min + currency), employment type** — bot commands + web fields + scoring prompt uses them~~ ✅ 2026-06-12 | 1.5 d | migration 6; `/role` `/salary` `/portfolio` bot commands; scoring prompt caps low-ball/wrong-family listings at 5 |
| S2-2 | ~~Web CV upload + portfolio links on /account~~ ✅ 2026-06-12 | 1.5 d | `POST /api/me/cv` (same extraction pipeline as the bot); portfolio links appended to application emails |
| S2-3 | ~~`user.tier` + weekly apply quota + upgrade CTA~~ ✅ 2026-06-12 | 1 d | rolling 7-day window (no reset cron needed): 5/50/200; upgrade CTA in bot + Plan section with quota bar on /account; quota'd auto-applies fall back to notification |
| S2-4 | ~~Stripe Checkout + portal + webhook → tier~~ ✅ 2026-06-12 (code; needs STRIPE_* env + a test-mode purchase to verify end-to-end) | 2 d | REST via httpx (no SDK), signature verification tested, endpoints 503 until configured; `tier` not user-writable |
| S2-5 | ~~LLM budget + /run cooldown~~ ✅ 2026-06-12 | 1 d | 15-min /run cooldown; 80-jobs-per-run scoring cap (excess defers to next run); scoring already once-per-job by status design |
| S2-6 | Prod deploy: domain, fe hosting, `/setdomain`; ~~encrypt refresh tokens~~ ✅; backups — **deploy/domain/backup-drill are manual owner steps** | 1.5 d | Fernet `enc:`-prefixed tokens, transparent in db layer, legacy plaintext still readable, lost-key fails loud; set `TOKEN_ENCRYPTION_KEY` in prod |

### Sprint 3 (Wk 5–6) — "Status checks itself" → M2 window opens

| ID | Task | Size | Acceptance |
|---|---|---|---|
| S3-1 | ~~Reply tracking: `gmail.readonly` opt-in, hourly poller, thread-match vs `applies` → `replied` + notification~~ ✅ 2026-06-12 | 4 d | `jobfox/reply_tracking.py`; migration 7; `POST /cron/replies` (hourly — **schedule it in GitHub Actions/cron-job.org**) + post-pipeline sweep; per-recipient Gmail search; pre-readonly grants get a one-time /connect_gmail nudge. **Needs live verification once a real reply arrives** |
| S3-2 | ~~AI reply classification → interview/offer/rejection events **+ interview date extraction**~~ ✅ 2026-06-12 | 2 d | Flash classify → status + auto event with summary + `interview_datetime` in payload (feeds S5-1 calendar); detection survives classifier failure |
| S3-3 | ~~Dashboard v2: full funnel viz, per-job timeline~~ ✅ 2026-06-12 (shareable funnel-card image deferred to S4 launch prep) | 2 d | Replies card + reply rate on web + /stats; click any job row → event timeline with auto badges |
| S3-4 | Notification settings + daily email digest (TG-ban hedge) | 1 d | opt-in digest ships |

### Sprint 4 (Wk 7–8) — "v1.0 public launch" → M3

| ID | Task | Size | Acceptance |
|---|---|---|---|
| S4-1 | PWA: manifest, service worker, web push | 2 d | installable; push fires on match/reply |
| S4-2 | QA pass: empty/error/loading states, mobile layouts | 1.5 d | checklist green |
| S4-3 | Launch: channel-admin posts, LinkedIn/PH, referral codes | 2 d | public launch executed |
| S4-4 | 10 user interviews scheduled from early cohort | 0.5 d | calendar booked |

### Sprint 5 (Wk 9–10) — "Interview copilot, part 1" (v1.5)

| ID | Task | Size | Acceptance |
|---|---|---|---|
| S5-1 | **Calendar**: interview dates (auto from S3-2 + manual entry), web calendar view, TG reminders day-before/hour-before | 2.5 d | upcoming interviews visible + reminded |
| S5-2 | **Expected questions** per job: Flash generation cached per job, role-family templates | 1.5 d | Pro user gets question list on interview status |
| S5-3 | **Market salary** per match: cached per (role, region); source = levels-style datasets + LLM synthesis, labeled as estimate | 1.5 d | salary band on each match card |
| S5-4 | **Company intel** card: site scrape + LinkedIn company page basics, cached per company | 2 d | company card on job page |

### Sprint 6 (Wk 11–12) — "Interview copilot, part 2 + 3 languages" 

| ID | Task | Size | Acceptance |
|---|---|---|---|
| S6-1 | **i18n EN/HY/RU**: fe (react-i18next), bot (message catalog), LLM outputs in user language; language picker on onboarding | 3 d | full journey usable in all three |
| S6-2 | **Mock interview** (Power): LLM interviewer in TG/web chat, per-job context, feedback summary | 2.5 d | capped sessions live behind Power |
| S6-3 | Offer analysis (Power): offer vs market, negotiation pointers | 1 d | offer event → analysis card |
| S6-4 | Pricing-page refresh from the feature matrix (§1) | 0.5 d | plans show journey stages |

### Sprints 7–8 (Wk 13–16) — "Mobile app + scale" (v2.0)

- **Mobile app**: Expo/React Native reusing the existing token auth +
  JSON API; same screens as web (dashboard, matches, calendar, account);
  push via FCM/APNs. Go/no-go decided on PWA usage data from S4-1.
- ATS discovery (Greenhouse/Lever/Ashby) to de-risk LinkedIn.
- Regional pricing experiment ($9–12 vs $19).
- Bootcamp pilot (one institution, free Pro cohort).
- B2B white-label groundwork.

---

## 4. Milestones

| Milestone | Definition of done | Target |
|---|---|---|
| **M0 — Applies reach humans** | extraction >60% overall, legal pages live, verification submitted | end S1 |
| **M1 — Sellable, full profile** | Stripe live, complete web onboarding (CV, role, salary), prod domain | end S2 |
| **M2 — First paying customer** | ≥1 stranger pays | during S3 |
| **M3 — v1.0 launched** | auto status tracking + funnel + PWA, public | end S4 |
| **M4 — Interview copilot live (v1.5)** | calendar, questions, salary, company intel, 3 languages | end S6 |
| **M5 — Mobile app (v2.0) / scale decision** | app shipped or data-backed no-go; 100 paying or pivot | ~6 months |

---

## 5. Business plan

### 5.1 Market & positioning

- **Beachhead:** Armenian tech job seekers (~30–50k addressable); then
  Georgia/Ukraine/MENA with the same local-bias engine. RU+HY+EN
  languages cover the beachhead *and* the first expansion ring.
- **Positioning:** "The AI job hunter that takes you from application to
  offer — and applies *as you*, not as a bot farm." Multi-surface:
  Telegram, web, mobile.
- **Competition:** global auto-apply tools (LazyApply, Simplify, AIApply)
  stop at "applied" — none do the tracking → interview-prep → offer
  layer, none are localized. Sofi (hh.ru) validates the model nearby.

### 5.2 Pricing

Free $0 (prove matching) · **Pro $19/mo** (automation: volume,
auto-apply, tracking, calendar, questions, salary) · Power $49/mo
(career coach: mock interviews, offer analysis, company intel, CV
variants). The §1 feature matrix is the source of truth. Annual "career
insurance" $99/yr post-launch; regional pricing experiment scheduled,
not assumed.

### 5.3 Go-to-market (cheapest first)

1. Pinned/sponsored posts in the indexed Telegram channels.
2. Funnel-card testimonials — every tracked offer is a story.
3. Bootcamps/career centers (AUA, TUMO, ACA): free cohort → B2B
   white-label ($5–10k/yr).
4. Referrals: give-a-week/get-a-week Pro.
5. Paid ads only after organic CAC data exists.

### 5.4 Targets

| Stage | Free | Paying | MRR | Watch metric |
|---|---|---|---|---|
| M1 | 50 | 0 | $0 | CV→first-apply ≥40% |
| M2 | 200 | 5 | ~$100 | free→pro ≥3% |
| M3 | 600 | 25 | ~$500 | wk-4 retention ≥30% |
| M4–M5 | 2000 | 100 | ~$2k | churn <15%/mo, CAC <$5 |

### 5.5 Kill criteria

- M2 slips >4 wks with ≥200 free users → 10 user interviews before more
  features.
- Wk-4 retention <15% at M3 → stop features, fix match/apply quality.
- OAuth verification rejected twice → copy-paste-first apply, re-plan.

---

## 6. Dependencies & critical path

```
S1-1/2 emails ──► applies reach humans ──► replies exist ──► S3 tracking has data
S1-4/5 legal ──► S1-6 OAuth verification ──► S3-1 readonly scope ──► S5 calendar dates
S2-1 salary/role profile ──► scoring quality ──► S5-3 salary features credible
S2-2 web onboarding ──► PWA (S4-1) ──► mobile app (S7) is meaningful
S3-2 date extraction ──► S5-1 calendar auto-fill
```

Longest external pole: **Google OAuth verification** (2–4 wks wall-clock)
— submitted at the end of Sprint 1 for exactly this reason.

## 7. Risk register

PRODUCT.md §Risks holds the core six (Google revocation, LinkedIn blocks,
LLM cost, quality drift, breach, Telegram ban). Plan-level:

- **Solo-dev burnout** → all tasks ≤4 days; ship weekly; sprints have one
  goal each.
- **Scope creep on the copilot layer** → S5/S6 features are cached,
  Flash-powered, per-job (not per-user) generations — cheap by
  construction; anything needing a new data vendor goes to backlog.
- **$19 too high for Armenia** → scheduled experiment, not assumption.
- **Verification delay** → S3-1 behind test-user flag; sell Pro on
  volume + prep while waiting.
- **staff.am changes embedded JSON** → S1-3 alarm catches it in one run.
