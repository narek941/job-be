import os
import httpx

api_key = os.environ.get("GEMINI_API_KEY")
url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
resp = httpx.get(url)
for m in resp.json().get("models", []):
    print(m["name"])
