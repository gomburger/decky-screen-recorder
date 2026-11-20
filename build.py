#!/usr/bin/env python3
"""Package the plugin for Decky's "Install Plugin from ZIP File".

Writes ../decky-screen-recorder-<version>.zip, taking the version from package.json.
"""

import json
import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent
NAME = "decky-screen-recorder"
FILES = (
    "main.py",
    "plugin.json",
    "package.json",
    "LICENSE",
    "README.md",
    "dist/index.js",
    "shaders/pq_to_sdr.glsl",
    "shaders/deck_hdr_to_sdr.glsl",
)

version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
output = ROOT.parent / f"{NAME}-{version}.zip"

with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
    for file in FILES:
        archive.write(ROOT / file, f"{NAME}/{file}")

print(output)
