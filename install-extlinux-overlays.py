#!/usr/bin/python3
import shutil
import sys
from pathlib import Path


path = Path(sys.argv[1])
required = sys.argv[2:]
if not required:
    raise SystemExit("No overlays supplied")
text = path.read_text(encoding="utf-8")
backup = path.with_suffix(path.suffix + ".meeting-assistant.bak")
if not backup.exists():
    shutil.copy2(path, backup)
lines = text.splitlines()
found = False
for index, line in enumerate(lines):
    stripped = line.strip()
    if stripped.startswith("fdtoverlays "):
        values = stripped.split()[1:]
        for overlay in required:
            if overlay not in values:
                values.append(overlay)
        indent = line[: len(line) - len(line.lstrip())]
        lines[index] = indent + "fdtoverlays  " + " ".join(values)
        found = True
        break
if not found:
    insert_at = next((i + 1 for i, line in enumerate(lines) if line.lstrip().startswith("fdt ")), len(lines))
    lines.insert(insert_at, "\tfdtoverlays  " + " ".join(required))
path.write_text("\n".join(lines) + "\n", encoding="utf-8")

