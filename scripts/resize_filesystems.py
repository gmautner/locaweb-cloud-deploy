#!/usr/bin/env python3
"""
Resize ext4 filesystems on VMs whose data disks were grown by CloudStack.

After `cmk resize volume` expands the block device, the ext4 filesystem
inside the VM does not grow automatically.  This script SSHes into each
affected VM and runs `resize2fs /dev/vdb` (online, no downtime).

Usage:
    python3 scripts/resize_filesystems.py \
        --ssh-key /tmp/ssh_key \
        --provision-output /tmp/provision-output.json
"""
import argparse
import json
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# SSH helpers (same pattern as configure_unattended_upgrades.py)
# ---------------------------------------------------------------------------

SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "ConnectTimeout=10",
    "-o", "BatchMode=yes",
]


def ssh_run(ip, command, key_path, retries=3):
    """Run a remote command via SSH. Retries on transient connection errors."""
    cmd = ["ssh"] + SSH_OPTS + ["-i", key_path, f"root@{ip}", command]
    for attempt in range(retries):
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 or "Connection reset" not in result.stderr:
            return result.returncode, result.stdout.strip(), result.stderr.strip()
        if attempt < retries - 1:
            time.sleep(5)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def wait_for_ssh(ip, key_path, timeout=180):
    """Poll SSH every 10s until it responds or timeout is reached."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            rc, _, _ = ssh_run(ip, "true", key_path)
            if rc == 0:
                return True
        except subprocess.TimeoutExpired:
            pass
        time.sleep(10)
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Resize ext4 filesystems after CloudStack volume expansion")
    parser.add_argument("--ssh-key", required=True,
                        help="Path to SSH private key")
    parser.add_argument("--provision-output", required=True,
                        help="Path to provision-output.json")
    args = parser.parse_args()

    with open(args.provision_output) as f:
        output = json.load(f)

    # Collect VMs whose data disks were resized
    targets = []
    if output.get("web_disk_resized"):
        web_ip = output.get("web_ip", "")
        if web_ip:
            targets.append(("web", web_ip))

    for name, data in output.get("accessories", {}).items():
        if data.get("disk_resized"):
            ip = data.get("ip", "")
            if ip:
                targets.append((name, ip))

    if not targets:
        print("No disks were resized — nothing to do.")
        return

    print(f"Expanding filesystem on {len(targets)} VM(s)...")
    failed = []
    for label, ip in targets:
        print(f"\n[{label}] Waiting for SSH on {ip}...")
        if not wait_for_ssh(ip, args.ssh_key, timeout=180):
            print(f"  [FAIL] SSH not reachable on {ip} after 180s")
            failed.append(ip)
            continue

        print(f"  Running resize2fs /dev/vdb...")
        rc, stdout, stderr = ssh_run(ip, "resize2fs /dev/vdb", args.ssh_key)
        if rc != 0:
            print(f"  [FAIL] resize2fs failed on {ip}: {stderr}")
            failed.append(ip)
        else:
            # Show new filesystem size
            rc2, df_out, _ = ssh_run(ip, "df -h /data", args.ssh_key)
            if rc2 == 0:
                lines = df_out.strip().split("\n")
                if len(lines) >= 2:
                    print(f"  OK — {lines[-1]}")
                else:
                    print(f"  OK")
            else:
                print(f"  OK")

    if failed:
        print(f"\n[ERROR] Filesystem resize failed on: {', '.join(failed)}")
        sys.exit(1)

    print(f"\nFilesystem resize completed successfully on all {len(targets)} VM(s).")


if __name__ == "__main__":
    main()
