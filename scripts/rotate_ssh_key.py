#!/usr/bin/env python3
"""
SSH key rotation script for Locaweb CloudStack VMs.

Rotates the SSH key for all VMs in a deployment:
1. Verify the keypair already exists in CloudStack (safety check)
2. Delete the old keypair from CloudStack
3. Register the same keypair name with the new public key
4. For each VM: stop, reset SSH key, start, purge old keys from authorized_keys

The caller is responsible for generating the new key and updating the
GitHub secret (steps 1-2 in the rotation procedure). This script handles
the CloudStack and VM-level rotation (steps 3-5).

VMs are rotated in order: accessories first, then workers, then web last,
to minimize user-facing downtime.

Usage:
    python3 scripts/rotate_ssh_key.py \\
        --network-name my-app-12345-preview \\
        --public-key /tmp/ssh_key.pub \\
        --ssh-key /tmp/ssh_key
"""
import argparse
import json
import subprocess
import sys
import time


CMK_MAX_RETRIES = 5
SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "ConnectTimeout=10",
    "-o", "BatchMode=yes",
]


def cmk(*args):
    """Run a cmk command and return parsed JSON with retries."""
    cmd = ["cmk"] + list(args)
    for attempt in range(CMK_MAX_RETRIES + 1):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            if not result.stdout.strip():
                return {}
            return json.loads(result.stdout)
        error_msg = result.stderr.strip() or result.stdout.strip()
        if attempt < CMK_MAX_RETRIES:
            backoff = 2 ** (attempt + 1)
            print(f"  Retry {attempt + 1}/{CMK_MAX_RETRIES}: cmk {' '.join(args)}: {error_msg} (backoff {backoff}s)")
            time.sleep(backoff)
        else:
            raise RuntimeError(
                f"cmk {' '.join(args)} failed after {CMK_MAX_RETRIES + 1} attempts: {error_msg}")


# ---------------------------------------------------------------------------
# SSH helpers
# ---------------------------------------------------------------------------

def ssh_run(ip, command, key_path, retries=3):
    """Run a command over SSH, return (returncode, stdout, stderr)."""
    cmd = ["ssh"] + SSH_OPTS + ["-i", key_path, f"root@{ip}", command]
    for attempt in range(retries):
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return result.returncode, result.stdout, result.stderr
        if attempt < retries - 1:
            time.sleep(5)
    return result.returncode, result.stdout, result.stderr


def wait_for_ssh(ip, key_path, timeout=300):
    """Wait until SSH is available on the given IP."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            rc, _, _ = ssh_run(ip, "true", key_path, retries=1)
            if rc == 0:
                return True
        except subprocess.TimeoutExpired:
            pass
        time.sleep(10)
    return False


# ---------------------------------------------------------------------------
# CloudStack helpers
# ---------------------------------------------------------------------------

def find_keypair(name):
    """Check if an SSH key pair exists in CloudStack."""
    data = cmk("list", "sshkeypairs", f"name={name}")
    if data and data.get("sshkeypair"):
        return True
    return False


def find_network(name):
    """Find network by name, return ID or None."""
    data = cmk("list", "networks", f"filter=id,name")
    if data:
        for n in data.get("network", []):
            if n["name"] == name:
                return n["id"]
    return None


def list_vms_in_network(network_id):
    """List all VMs in a network."""
    data = cmk("list", "virtualmachines", f"networkid={network_id}",
               "filter=id,name,state")
    if data:
        return data.get("virtualmachine", [])
    return []


def get_vm_public_ip(vm_id, network_id):
    """Get the public IP mapped to a VM via static NAT."""
    data = cmk("list", "publicipaddresses",
               f"associatednetworkid={network_id}",
               "filter=id,ipaddress,virtualmachineid")
    if data:
        for ip in data.get("publicipaddress", []):
            if ip.get("virtualmachineid") == vm_id:
                return ip["ipaddress"]
    return None


def wait_for_vm_state(vm_id, target_state, timeout=300):
    """Poll until a VM reaches the target state."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = cmk("list", "virtualmachines", f"id={vm_id}", "filter=id,state")
        if data and data.get("virtualmachine"):
            state = data["virtualmachine"][0].get("state", "")
            if state == target_state:
                return True
        time.sleep(5)
    return False


# ---------------------------------------------------------------------------
# Rotation logic
# ---------------------------------------------------------------------------

def classify_vms(vms):
    """Classify VMs into web, workers, and accessories for rotation order.

    VM names are short: "web", "worker-1", "db", etc. (not prefixed with
    the network name).
    """
    web = []
    workers = []
    accessories = []

    for vm in vms:
        name = vm["name"]
        if name == "web":
            web.append(vm)
        elif name.startswith("worker-"):
            workers.append(vm)
        else:
            accessories.append(vm)

    # Rotation order: accessories first, then workers, then web last
    return accessories + workers + web


def rotate_vm(vm, network_id, keypair_name, public_key, ssh_key_path):
    """Rotate SSH key for a single VM: stop, reset, start, purge old keys."""
    vm_id = vm["id"]
    vm_name = vm["name"]
    vm_state = vm.get("state", "Unknown")

    print(f"\n  --- {vm_name} (state={vm_state}) ---")

    # Get public IP before stopping
    public_ip = get_vm_public_ip(vm_id, network_id)
    if not public_ip:
        print(f"  ERROR: No public IP found for {vm_name}. Skipping.")
        return False

    # Step 1: Stop the VM (if not already stopped)
    if vm_state != "Stopped":
        print(f"  Stopping {vm_name}...")
        cmk("stop", "virtualmachine", f"id={vm_id}")
        if not wait_for_vm_state(vm_id, "Stopped"):
            print(f"  ERROR: {vm_name} did not stop within timeout. Skipping.")
            return False
        print(f"  Stopped.")

    # Step 2: Reset SSH key
    print(f"  Resetting SSH key...")
    result = cmk("reset", "sshkeyforvirtualmachine",
                  f"id={vm_id}", f"keypair={keypair_name}")
    if not result:
        print(f"  ERROR: Failed to reset SSH key for {vm_name}.")
        # Still try to start the VM
        cmk("start", "virtualmachine", f"id={vm_id}")
        wait_for_vm_state(vm_id, "Running")
        return False
    print(f"  SSH key reset.")

    # Step 3: Start the VM
    print(f"  Starting {vm_name}...")
    cmk("start", "virtualmachine", f"id={vm_id}")
    if not wait_for_vm_state(vm_id, "Running"):
        print(f"  ERROR: {vm_name} did not start within timeout.")
        return False
    print(f"  Running.")

    # Step 4: Purge old keys from authorized_keys
    print(f"  Waiting for SSH on {public_ip}...")
    if not wait_for_ssh(public_ip, ssh_key_path):
        print(f"  ERROR: SSH not available on {public_ip} within timeout.")
        return False

    print(f"  Purging old keys from authorized_keys...")
    rc, _, stderr = ssh_run(
        public_ip,
        f"echo '{public_key}' > /root/.ssh/authorized_keys",
        ssh_key_path)
    if rc != 0:
        print(f"  ERROR: Failed to purge old keys: {stderr}")
        return False
    print(f"  authorized_keys updated.")

    # Verify
    rc, stdout, _ = ssh_run(
        public_ip,
        "wc -l < /root/.ssh/authorized_keys",
        ssh_key_path)
    if rc == 0:
        line_count = stdout.strip()
        print(f"  Verified: {line_count} key(s) in authorized_keys.")

    return True


def rotate(network_name, public_key_path, ssh_key_path):
    """Main rotation procedure."""
    keypair_name = f"{network_name}-key"

    # Read public key
    with open(public_key_path) as f:
        public_key = f.read().strip()

    # Pre-flight: keypair must already exist
    print(f"[1/4] Verifying keypair '{keypair_name}' exists...")
    if not find_keypair(keypair_name):
        print(f"ERROR: Keypair '{keypair_name}' does not exist in CloudStack.")
        print("This workflow only rotates keys for existing deployments.")
        sys.exit(1)
    print(f"  Found.")

    # Pre-flight: network must exist
    print(f"[2/4] Finding network '{network_name}'...")
    net_id = find_network(network_name)
    if not net_id:
        print(f"ERROR: Network '{network_name}' not found.")
        sys.exit(1)
    print(f"  Found: {net_id}")

    # List VMs
    vms = list_vms_in_network(net_id)
    if not vms:
        print(f"ERROR: No VMs found in network '{network_name}'.")
        sys.exit(1)
    print(f"  VMs: {', '.join(vm['name'] for vm in vms)}")

    # Replace keypair in CloudStack
    print(f"[3/4] Replacing keypair in CloudStack...")
    cmk("delete", "sshkeypair", f"name={keypair_name}")
    print(f"  Deleted old keypair.")
    cmk("register", "sshkeypair",
        f"name={keypair_name}",
        f"publickey={public_key}")
    print(f"  Registered new keypair.")

    # Rotate each VM
    ordered_vms = classify_vms(vms)
    print(f"[4/4] Rotating SSH key on {len(ordered_vms)} VM(s)...")

    failed = []
    for vm in ordered_vms:
        success = rotate_vm(vm, net_id, keypair_name, public_key, ssh_key_path)
        if not success:
            failed.append(vm["name"])

    # Summary
    print(f"\n{'='*60}")
    if failed:
        print(f"SSH key rotation completed with errors.")
        print(f"Failed VMs: {', '.join(failed)}")
        print(f"{'='*60}")
        sys.exit(1)
    else:
        print(f"SSH key rotation complete! All {len(ordered_vms)} VM(s) updated.")
        print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(
        description="Rotate SSH keys on deployed CloudStack VMs")
    parser.add_argument("--network-name", required=True,
                        help="Network name (<repo-name>-<repo-id>-<env-name>)")
    parser.add_argument("--public-key", required=True,
                        help="Path to new SSH public key file")
    parser.add_argument("--ssh-key", required=True,
                        help="Path to new SSH private key file")
    args = parser.parse_args()

    rotate(args.network_name, args.public_key, args.ssh_key)


if __name__ == "__main__":
    main()
