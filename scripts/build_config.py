#!/usr/bin/env python3
"""Build deployment configuration JSON from workflow inputs.

Reads INPUT_* environment variables and writes /tmp/config.json.
"""
import json
import os
import re
import sys

accessories_raw = os.environ.get("INPUT_ACCESSORIES") or "[]"
try:
    accessories = json.loads(accessories_raw)
except json.JSONDecodeError:
    accessories = []

# Accessory names become VM names and env-var suffixes (INFRA_{NAME}_IP),
# so they must be lowercase alphanumeric with underscores only.
for acc in accessories:
    name = acc.get("name", "")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        print(f"ERROR: Invalid accessory name '{name}'. "
              "Names must start with a letter and contain only "
              "lowercase letters, digits, and underscores (no hyphens).",
              file=sys.stderr)
        sys.exit(1)

config = {
    "zone": os.environ.get("INPUT_ZONE") or "ZP01",
    "web_plan": os.environ.get("INPUT_WEB_PLAN") or "small",
    "web_disk_size_gb": int(os.environ.get("INPUT_WEB_DISK_SIZE_GB") or "20"),
    "workers_replicas": int(os.environ.get("INPUT_WORKERS_REPLICAS") or "0"),
    "workers_plan": os.environ.get("INPUT_WORKERS_PLAN") or "small",
    "accessories": accessories,
    "recover": os.environ.get("INPUT_RECOVER") == "true",
}

with open("/tmp/config.json", "w") as f:
    json.dump(config, f, indent=2)

print("Configuration:")
for k, v in config.items():
    print(f"  {k}: {v}")
