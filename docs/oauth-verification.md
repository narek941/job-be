# Google OAuth verification — submission checklist

Goal: remove the "This app isn't verified" screen and unlock more than
100 test users. Wall-clock: 2–4 weeks of review back-and-forth, so submit
as early as possible. **This is a manual step in Google Cloud Console —
only the project owner can do it.**

## Scopes to request (request BOTH now to avoid a second review)

| Scope | Why we say we need it (use this wording) |
|---|---|
| `gmail.compose` | "Creates email drafts of job applications (cover letter + the user's CV attached) in the user's own Gmail account, which the user reviews and sends themselves." |
| `gmail.readonly` | "Detects recruiter replies to applications the user sent, so the product can update the application's status (replied / interview / rejected) and notify the user. Read access is limited in practice to thread-matching against messages the product itself drafted." |
| `userinfo.email`, `openid` | Account identification — shows the user which Gmail they connected. |

`gmail.readonly` is a **restricted** scope → expect the security
assessment questionnaire; possibly a third-party assessment requirement
at scale (CASA Tier 2). Budget for it; answer honestly that data is
stored in Supabase Postgres, encrypted in transit, tokens never exposed
client-side.

## Prerequisites (all DONE as of 2026-06-12 — verify before submitting)

- [x] Privacy Policy live at `{APP_URL}/privacy` ← required link
- [x] Terms of Service live at `{APP_URL}/terms`
- [x] Data deletion: `/delete_me` in bot + DELETE /api/me (mention in the
      privacy policy — reviewers check)
- [x] Data export: GET /api/me/export
- [ ] App homepage live at `{APP_URL}` (deploy job-fe to the prod domain
      first — reviewers visit it)
- [ ] Real support email in the legal pages (currently a TODO constant in
      `job-fe/src/pages/Legal.tsx` — set it before submitting)

## Console steps

1. console.cloud.google.com → APIs & Services → **OAuth consent screen**.
2. App name **JobFox**, support email, logo (use
   `jobfox/static/avatar.png`, 120×120 version), domains: add the prod
   domain; links to /terms and /privacy.
3. **Scopes** tab → add the four scopes above with the justifications.
4. **Demo video** (required for sensitive/restricted scopes): screen
   recording showing: login → connect Gmail consent → Apply tap → draft
   appearing in Gmail with CV attached → (for readonly) status flipping
   after a reply. Keep it under 3 minutes, unlisted YouTube link.
5. Submit → answer reviewer emails within 24 h (slow answers reset your
   place in the queue).

## While waiting

- The app keeps working for up to 100 **test users** added under
  "Test users" on the consent screen — add early adopters there.
- Refresh tokens issued in Testing mode expire after 7 days ONLY for
  apps with a "Testing" publishing status + external user type — moving
  to "In production (unverified)" avoids that but shows the warning
  screen. Choose based on how many early users complain.
