#!/usr/bin/python3
import json
import os
import tempfile
import urllib.parse
import urllib.request


def read_env(path):
    values = {}
    order = []
    with open(path, encoding="utf-8") as source:
        for raw_line in source:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key not in values:
                order.append(key)
            values[key] = value.strip().strip("\"'")
    return values, order


source, _ = read_env("/root/meeting_assistant.key")
target, order = read_env("/etc/meeting-assistant.env")
token = source.get("VK_GROUP_ACCESS_KEY", "")
if not token:
    raise SystemExit("VK_GROUP_ACCESS_KEY_MISSING")
target["VK_GROUP_TOKEN"] = token
if "VK_GROUP_TOKEN" not in order:
    order.append("VK_GROUP_TOKEN")

fd, temporary = tempfile.mkstemp(dir="/etc", prefix="meeting-assistant.env.")
try:
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        for key in order:
            output.write(f"{key}={target[key]}\n")
    os.chmod(temporary, 0o640)
    os.chown(temporary, 0, __import__("grp").getgrnam("meetingassistant").gr_gid)
    os.replace(temporary, "/etc/meeting-assistant.env")
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)

query = urllib.parse.urlencode(
    {"group_id": "240883110", "access_token": token, "v": "5.199"}
)
with urllib.request.urlopen(f"https://api.vk.com/method/groups.getById?{query}", timeout=20) as response:
    result = json.load(response)
if "error" in result:
    raise SystemExit(f"VK_API_ERROR={result['error'].get('error_code')}:{result['error'].get('error_msg')}")
group = (result.get("response", {}).get("groups") or result.get("response") or [{}])[0]
print("VK_TOKEN_OK=true")
print(f"VK_GROUP_ID={group.get('id', '')}")
print(f"VK_GROUP_NAME={group.get('name', '')}")
