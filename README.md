<p align="center"><img src="jobfox/static/logo.svg" alt="JobFox" width="160"></p>

# JobFox

AI job hunter — in Telegram, on the web, and (soon) on mobile.
Uploads a CV once, discovers jobs daily,
scores fit, drafts grounded cover letters, and drops a real Gmail draft
(with the CV attached) into the user's own Gmail on a single tap.

📖 **Start here:** [PRODUCT.md](./PRODUCT.md) — what it is, why it
exists, how it works, where the code lives, deployment, roadmap, and
monetization plan.

## Quick start

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.test .env       # fill in real creds (see PRODUCT.md → Configuration)
.venv/bin/uvicorn jobfox.main:app --reload --port 8000
.venv/bin/python -m pytest tests/ -q
```

Env vars, OAuth setup, and the daily cron contract are all documented
in [PRODUCT.md → Configuration](./PRODUCT.md#configuration) and
[Operations](./PRODUCT.md#operations).
