#!/usr/bin/python3
import json
import urllib.request

values = {}
with open("/root/meeting_assistant.key", encoding="utf-8") as source:
    for raw_line in source:
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")

token = values.get("TELEGRAM_KEY", "")
if not token:
    raise SystemExit("TELEGRAM_KEY_MISSING")

with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=15) as response:
    data = json.load(response)
result = data.get("result", {})
print(f"TELEGRAM_OK={data.get('ok', False)}")
print(f"BOT_ID={result.get('id', '')}")
print(f"BOT_USERNAME={result.get('username', '')}")
