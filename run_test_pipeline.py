import json
from armapply.users_db import get_user_by_email, _exec
from armapply.scheduler import run_pipeline_for_user

u = get_user_by_email("test@test.com")
uid = u["id"]

print("Starting pipeline execution...")
res = run_pipeline_for_user(uid)
print("\n--- PIPELINE RESULT ---")
print(json.dumps(res, indent=2))

print("\n--- TOP SCORED JOB ---")
row = _exec("SELECT title, site, location, fit_score, cover_letter_text FROM jobs WHERE user_id = %s ORDER BY fit_score DESC LIMIT 1", (uid,), fetch="one")
if row:
    print(f"Title: {row.get('title')}")
    print(f"Company: {row.get('site')}")
    print(f"Score: {row.get('fit_score')}/10")
    print(f"Cover Letter Preview:\n{row.get('cover_letter_text')[:300]}...\n")
else:
    print("No jobs found or scored.")
