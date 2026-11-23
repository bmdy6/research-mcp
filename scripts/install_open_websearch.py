#!/usr/bin/env python3
"""Explicit optional install; never called by search or MCP startup."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

REVISION = "2562c9233a945c18fa9d08b7e450ca218a8853b6"
root = Path.home() / ".cache/research-mcp/open-websearch" / REVISION
for command in ("git", "node", "npm"):
    if not shutil.which(command):
        sys.exit(f"Missing {command}")
if int(subprocess.check_output(["node", "--version"], text=True).strip().lstrip("v").split(".")[0]) < 20:
    sys.exit("Node.js >=20 is required")
if not root.exists():
    root.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--no-checkout", "https://github.com/Aas-ee/open-webSearch.git", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "checkout", "--detach", REVISION], check=True)
actual = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
if actual != REVISION:
    sys.exit("Existing cache has a different revision; refusing to overwrite")
package = json.loads((root / "package.json").read_text())
if package.get("name") != "open-websearch" or package.get("version") != "2.2.0":
    sys.exit("Unexpected upstream package")
# No browser download or dependency lifecycle scripts. Request engines need no browser.
subprocess.run(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=root, check=True)
subprocess.run(["npm", "run", "build"], cwd=root, check=True)
print(f"Installed pinned request-only backend: {root}")
