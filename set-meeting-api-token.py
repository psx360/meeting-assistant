#!/usr/bin/python3
import grp
import os
import tempfile


def read(path):
    values, order = {}, []
    with open(path, encoding="utf-8") as source:
        for raw in source:
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                if key not in values:
                    order.append(key)
                values[key] = value
    return values, order


source, _ = read("/tmp/meeting-upload.env")
target, order = read("/etc/meeting-assistant.env")
target["MEETING_API_TOKEN"] = source["MEETING_API_TOKEN"]
if "MEETING_API_TOKEN" not in order:
    order.append("MEETING_API_TOKEN")
fd, temporary = tempfile.mkstemp(dir="/etc", prefix="meeting-assistant.env.")
with os.fdopen(fd, "w", encoding="utf-8") as output:
    for key in order:
        output.write(f"{key}={target[key]}\n")
os.chmod(temporary, 0o640)
os.chown(temporary, 0, grp.getgrnam("meetingassistant").gr_gid)
os.replace(temporary, "/etc/meeting-assistant.env")
print("MEETING_API_TOKEN_UPDATED=true")
