#!/usr/bin/python3
import http.client
import json
import os
import sys
import urllib.parse


url, filename, token, meeting_id, progress_file = sys.argv[1:]
parsed = urllib.parse.urlsplit(url)
connection_class = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
port = parsed.port or (443 if parsed.scheme == "https" else 80)
size = os.path.getsize(filename)


def progress(percent):
    temporary = progress_file + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump({"phase": "upload", "percent": percent, "meeting_id": meeting_id}, stream)
    os.replace(temporary, progress_file)


connection = connection_class(parsed.hostname, port, timeout=60)
path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
connection.putrequest("POST", path)
connection.putheader("Authorization", f"Bearer {token}")
connection.putheader("Content-Type", "audio/ogg")
connection.putheader("Content-Length", str(size))
connection.putheader("X-Meeting-ID", meeting_id)
connection.endheaders()

sent = 0
with open(filename, "rb") as stream:
    while block := stream.read(1024 * 1024):
        connection.send(block)
        sent += len(block)
        progress(min(99, int(sent * 100 / size)))

response = connection.getresponse()
body = response.read()
if not 200 <= response.status < 300:
    raise RuntimeError(f"upload failed: HTTP {response.status}: {body[:1000]!r}")
progress(100)
sys.stdout.buffer.write(body)
sys.stdout.write("\n")
