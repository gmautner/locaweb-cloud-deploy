#!/usr/bin/env python3
"""
Infrastructure teardown script for Locaweb CloudStack deployment.

Destroys all CloudStack resources belonging to a project, identified by
its network name (<repo-name>-<repo-id>).

Destruction order (reverse of creation):
1. Snapshot policies
2. Detach and delete data volumes
3. Disable static NAT
4. Firewall rules
5. Release public IPs
6. Destroy VMs
7. Delete network
8. Delete SSH key pair

Usage:
    python3 scripts/teardown_infrastructure.py --network-name my-app-123456789
"""
import argparse
import json
import subprocess
import sys
import time


CMK_MAX_RETRIES = 5


def cmk(*args):
    """Run a cmk command and return parsed JSON.

    Retries up to CMK_MAX_RETRIES times with exponential backoff
    (2, 4, 8, 16, 32s) to handle intermittent CloudStack API errors.
    Unlike the provisioning script, final errors are non-fatal warnings
    since resources may already be partially deleted.
    """
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
            print(f"  Warning: cmk {' '.join(args)} failed after {CMK_MAX_RETRIES + 1} attempts: {error_msg}")
            return None


def teardown(network_name):
    """Destroy all resources for a project."""
    keypair_name = f"{network_name}-key"

    print(f"\n{'='*60}")
    print(f"Tearing down: {network_name}")
    print(f"{'='*60}\n")

    # Find the network
    data = cmk("list", "networks", "filter=id,name")
    net_id = None
    if data:
        for n in data.get("network", []):
            if n["name"] == network_name:
                net_id = n["id"]
                break
    if not net_id:
        print(f"  Network '{network_name}' not found. Nothing to tear down.")
        return

    # Find all VMs in this network
    data = cmk("list", "virtualmachines", f"networkid={net_id}",
               "filter=id,name,state")
    vms = data.get("virtualmachine", []) if data else []

    # 1. Delete snapshot policies for data volumes
    print("[1/8] Removing snapshot policies...")
    data = cmk("list", "volumes", "type=DATADISK",
               "tags[0].key=locaweb-ai-deploy-id",
               f"tags[0].value={network_name}",
               "filter=id,name")
    volumes = data.get("volume", []) if data else []
    for vol in volumes:
        policies = cmk("list", "snapshotpolicies", f"volumeid={vol['id']}")
        if policies and policies.get("snapshotpolicy"):
            for p in policies["snapshotpolicy"]:
                cmk("delete", "snapshotpolicy", f"id={p['id']}")
                print(f"  Deleted snapshot policy for {vol['name']}")

    # 2. Detach and delete data volumes
    print("[2/8] Detaching and deleting data volumes...")
    for vol in volumes:
        cmk("detach", "volume", f"id={vol['id']}")
        print(f"  Detached {vol['name']}")
        time.sleep(2)
        cmk("delete", "volume", f"id={vol['id']}")
        print(f"  Deleted {vol['name']}")

    # 3. Disable static NAT
    print("[3/8] Disabling static NAT...")
    ip_data = cmk("list", "publicipaddresses",
                  f"associatednetworkid={net_id}",
                  "filter=id,ipaddress,issourcenat,isstaticnat")
    ips = []
    if ip_data:
        for ip in ip_data.get("publicipaddress", []):
            if not ip.get("issourcenat", False):
                ips.append(ip)
    for ip in ips:
        if ip.get("isstaticnat", False):
            cmk("disable", "staticnat", f"ipaddressid={ip['id']}")
            print(f"  Disabled static NAT on {ip['ipaddress']}")

    # 4. Delete firewall rules
    print("[4/8] Deleting firewall rules...")
    for ip in ips:
        rules = cmk("list", "firewallrules", f"ipaddressid={ip['id']}",
                     "filter=id,startport,endport")
        if rules:
            for r in rules.get("firewallrule", []):
                cmk("delete", "firewallrule", f"id={r['id']}")
                print(f"  Deleted FW rule {r.get('startport')}-{r.get('endport')} on {ip['ipaddress']}")

    # 5. Release public IPs
    print("[5/8] Releasing public IPs...")
    for ip in ips:
        cmk("disassociate", "ipaddress", f"id={ip['id']}")
        print(f"  Released {ip['ipaddress']}")

    # 6. Destroy VMs
    print("[6/8] Destroying VMs...")
    for vm in vms:
        cmk("destroy", "virtualmachine", f"id={vm['id']}", "expunge=true")
        print(f"  Destroyed {vm['name']}")

    # 7. Delete network
    print("[7/8] Deleting network...")
    time.sleep(5)  # Wait for VMs to fully expunge
    cmk("delete", "network", f"id={net_id}")
    print(f"  Deleted {network_name}")

    # 8. Delete SSH key pair
    print("[8/8] Deleting SSH key pair...")
    cmk("delete", "sshkeypair", f"name={keypair_name}")
    print(f"  Deleted {keypair_name}")

    print(f"\n{'='*60}")
    print("Teardown complete!")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Tear down CloudStack infrastructure for a project")
    parser.add_argument("--network-name", required=True,
                        help="Network name (<repo-name>-<repo-id>)")
    args = parser.parse_args()

    teardown(args.network_name)


if __name__ == "__main__":
    main()
