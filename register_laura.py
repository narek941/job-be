#!/usr/bin/env python3
import os
import sys
from pathlib import Path

# Fix path to import armapply
sys.path.insert(0, str(Path(__file__).parent))

from armapply.users_db import create_user, update_user_preferences, get_user_by_email, _exec
from armapply.auth_deps import hash_password

def setup_laura():
    email = "arakelyanlaura0@gmail.com"
    password = "LauraPass123!" # Temporary password
    
    existing = get_user_by_email(email)
    if existing:
        uid = existing["id"]
        print(f"User Laura already exists with ID: {uid}. Updating preferences...")
    else:
        uid = create_user(email, hash_password(password))
        print(f"Created new user Laura with ID: {uid}")
    
    # Set preferences
    prefs = {
        "full_name": "Laura Arakelyan",
        "target_roles": ["QA Intern", "Junior QA Engineer", "Manual QA", "Data Analyst"],
        "target_location": "Armenia",
        "auto_pilot": True
    }
    update_user_preferences(uid, prefs)
    
    # Set Telegram chat ID if provided (this helps with notifications)
    # The user didn't provide a chat_id yet, but I'll leave a placeholder or if they use the app it will update.
    # update_user_telegram(uid, "some_id")
    
    print("✓ Laura's account and preferences are set up.")
    print(f"Login Email: {email}")
    print(f"Temp Password: {password}")

if __name__ == "__main__":
    setup_laura()
