#!/usr/bin/env python3
"""Build deployment configuration JSON from workflow inputs.

Reads INPUT_* environment variables and writes /tmp/config.json.
"""
import json
import os

config = {
    "zone": os.environ.get("INPUT_ZONE") or "ZP01",
    "domain": os.environ.get("INPUT_DOMAIN") or "",
    "web_plan": os.environ.get("INPUT_WEB_PLAN") or "small",
    "blob_disk_size_gb": int(os.environ.get("INPUT_BLOB_DISK_SIZE_GB") or "20"),
    "workers_enabled": os.environ.get("INPUT_WORKERS_ENABLED") == "true",
    "workers_replicas": int(os.environ.get("INPUT_WORKERS_REPLICAS") or "1"),
    "workers_plan": os.environ.get("INPUT_WORKERS_PLAN") or "small",
    "db_enabled": os.environ.get("INPUT_DB_ENABLED") == "true",
    "db_plan": os.environ.get("INPUT_DB_PLAN") or "medium",
    "db_disk_size_gb": int(os.environ.get("INPUT_DB_DISK_SIZE_GB") or "20"),
    "recover": os.environ.get("INPUT_RECOVER") == "true",
}

with open("/tmp/config.json", "w") as f:
    json.dump(config, f, indent=2)

print("Configuration:")
for k, v in config.items():
    print(f"  {k}: {v}")
