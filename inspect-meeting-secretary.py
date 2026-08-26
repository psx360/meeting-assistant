#!/usr/bin/python3
from pathlib import Path
import os
import sqlite3


locations = list(Path("/root").glob("meeting_secretary.key")) + list(Path("/opt/meeting-assistant").glob("**/meeting_secretary.key"))
print("KEY_LOCATIONS")
for path in locations:
    stat = path.stat()
    print(f"mode={stat.st_mode & 0o777:o} size={stat.st_size} path={path}")
    line = path.read_text(encoding="utf-8").strip()
    if "=" in line:
        print(f"format=key_value name={line.split('=', 1)[0].strip()}")
    else:
        print("format=raw_token")

print("SUBSCRIBERS")
db = sqlite3.connect("/var/lib/meeting-assistant/meeting-assistant.sqlite3")
for row in db.execute("SELECT platform, active, count(*) FROM subscribers GROUP BY platform, active"):
    print(f"platform={row[0]} active={row[1]} count={row[2]}")
