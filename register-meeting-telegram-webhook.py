#!/usr/bin/python3
import json
import urllib.parse
import urllib.request


values = {}
with open("/etc/meeting-assistant.env", encoding="utf-8") as source:
    for raw_line in source:
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip().strip("\"'")

token = values["TELEGRAM_KEY"]
secret = values["TELEGRAM_WEBHOOK_SECRET"]
base_url = values["PUBLIC_BASE_URL"].rstrip("/")
fields = urllib.parse.urlencode(
    {
        "url": base_url + "/telegram/webhook",
        "secret_token": secret,
        "allowed_updates": json.dumps(["message"]),
        "drop_pending_updates": "true",
    }
).encode()
request = urllib.request.Request(f"https://api.telegram.org/bot{token}/setWebhook", data=fields)
with urllib.request.urlopen(request, timeout=20) as response:
    result = json.load(response)
print(f"WEBHOOK_OK={result.get('ok', False)}")
print(f"DESCRIPTION={result.get('description', '')}")
