import json
import os
from armapply.users_db import get_user_by_email, update_user_preferences, save_user_resume, update_user_telegram

def setup_test_user():
    u = get_user_by_email("test@test.com")
    if not u:
        print("User not found")
        return
    
    uid = u["id"]

    # Set resume text
    resume = """
    Narek Qolyan
    Software Engineer
    Experience:
    - 5 years of Python/FastAPI backend development
    - React, React Native, Typescript frontend
    - Supabase, PostgreSQL
    - LLM Integration, Langchain, Prompt Engineering
    Looking for remote or Yerevan-based roles.
    """
    save_user_resume(uid, resume)

    # Set preferences
    prefs = json.loads(u.get("preferences_json") or "{}")
    prefs["telegram_bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN")
    
    # We will need a chat ID to actually send the message.
    # update_user_telegram(uid, "<YOUR_CHAT_ID>")
    
    prefs["search_config"] = {
        "queries": [{"query": "Python Developer"}],
        "locations": [{"location": "Remote", "remote": True}, {"location": "Yerevan", "remote": False}],
        "staff_am": {"enabled": True, "max_pages_per_keyword": 1},
        "defaults": {"results_per_site": 3}
    }
    update_user_preferences(uid, prefs)
    print("User profile, resume, and search preferences configured.")

if __name__ == "__main__":
    setup_test_user()
