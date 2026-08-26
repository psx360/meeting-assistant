#!/usr/bin/python3
import grp
import os
import tempfile


def read_env(path):
    values, order = {}, []
    with open(path, encoding="utf-8") as source:
        for raw in source:
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                if key not in values:
                    order.append(key)
                values[key] = value.strip().strip("\"'")
    return values, order


source, _ = read_env("/opt/transcribe-bot/.env")
target, order = read_env("/etc/meeting-assistant.env")
if not source.get("OPENAI_API_KEY"):
    raise SystemExit("OPENAI_API_KEY_MISSING")
target["OPENAI_API_KEY"] = source["OPENAI_API_KEY"]
for key in ("OPENAI_API_KEY", "OPENAI_MEETING_MODEL"):
    if key not in order:
        order.append(key)
target.setdefault("OPENAI_MEETING_MODEL", "gpt-5.6-terra")

fd, temporary = tempfile.mkstemp(dir="/etc", prefix="meeting-assistant.env.")
try:
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        for key in order:
            output.write(f"{key}={target[key]}\n")
    os.chmod(temporary, 0o640)
    os.chown(temporary, 0, grp.getgrnam("meetingassistant").gr_gid)
    os.replace(temporary, "/etc/meeting-assistant.env")
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
print("OPENAI_KEY_INSTALLED=true")
