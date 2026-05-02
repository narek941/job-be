import json
from armapply.users_db import get_user_by_email, get_user_preferences
u = get_user_by_email("test@test.com")
if u:
    print(f"Chat ID: {u.get('telegram_chat_id')}")
    prefs = get_user_preferences(u["id"])
    print(f"Prefs Chat ID: {prefs.get('telegram_chat_id')}")
else:
    print("User not found")
