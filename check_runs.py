import sys
from armapply.users_db import _exec
rows = _exec("SELECT stage, status, created_at FROM pipeline_runs WHERE user_id = 2 ORDER BY id DESC LIMIT 5", fetch="all")
for r in rows:
    print(r)
