import os
import httpx

api_key = os.environ.get("GEMINI_API_KEY")
model = os.environ.get("LLM_MODEL")

payload = {
    "contents": [{"role": "user", "parts": [{"text": "Hello"}]}],
}
url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
resp = httpx.post(
    url, json=payload,
    headers={"Content-Type": "application/json"},
    params={"key": api_key},
)
print(f"Status: {resp.status_code}")
print(resp.text)
