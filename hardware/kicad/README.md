# KiCad hardware schematic

Editable schematic for the assembled Meeting Assistant. It documents wiring to the Radxa ROCK 2F header; it is not a custom PCB layout.

Open `meeting-assistant.kicad_pro` with KiCad 10, or regenerate the schematic with the Python API used by the KiCad MCP ecosystem:

```powershell
$env:KICAD_SYMBOL_DIR="C:\Program Files\KiCad\10.0\share\kicad\symbols"
python -m pip install kicad-sch-api
python generate_schematic.py
```

For a per-user KiCad installation, adjust `KICAD_SYMBOL_DIR` to `%LOCALAPPDATA%\Programs\KiCad\10.0\share\kicad\symbols`.

The net labels encode the actual physical-pin mapping. Equal labels are electrically connected even where a long wire is intentionally avoided for readability.
