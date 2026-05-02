from armapply.llm_client import get_client
client = get_client()
print(f"Provider URL: {client.base_url}")
try:
    print("Testing OpenAI Compat:")
    print(client.ask("Hello! Who are you?"))
except Exception as e:
    print(f"Failed OpenAI Compat: {e}")

try:
    print("Testing Native:")
    client._use_native_gemini = True
    print(client.ask("Hello native!"))
except Exception as e:
    print(f"Failed native: {e}")
