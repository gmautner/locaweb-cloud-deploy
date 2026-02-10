#!/usr/bin/env python3
"""
Infrastructure provisioning script for Locaweb CloudStack deployment.

Creates CloudStack resources based on a validated JSON configuration:
- Isolated network with SSH key pair
- Web VM (always) with blob data disk
- Worker VMs (optional, N replicas, stateless — no data disks)
- Database VM (optional) with database data disk
- Public IPs with static NAT (1:1 mapping per VM)
- Firewall rules (SSH+HTTP+HTTPS for web; SSH only for workers and db)
- Daily snapshot policies for data disks

The script is idempotent — running it twice will skip existing resources.

Usage:
    python3 scripts/provision_infrastructure.py \\
        --repo-name my-app \\
        --unique-id 12345 \\
        --config /tmp/config.json
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

NETWORK_OFFERING_NAME = "Default Guest Network"
DISK_OFFERING_NAME = "data.disk.general"
TEMPLATE_REGEX = re.compile(r"^Ubuntu.*24.*$")
SNAPSHOT_SCHEDULE = "00:03"
SNAPSHOT_MAX = 3
SNAPSHOT_TIMEZONE = "America/Sao_Paulo"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_USERDATA = os.path.join(SCRIPT_DIR, "userdata", "web_vm.sh")
DB_USERDATA = os.path.join(SCRIPT_DIR, "userdata", "db_vm.sh")

# ---------------------------------------------------------------------------
# CloudMonkey helpers
# ---------------------------------------------------------------------------

def cmk(*args):
    """Run a cmk command and return parsed JSON."""
    cmd = ["cmk"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        error_msg = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"cmk {' '.join(args)} failed: {error_msg}")
    if not result.stdout.strip():
        return {}
    return json.loads(result.stdout)


def cmk_quiet(*args):
    """Run cmk, return None on error instead of raising."""
    try:
        return cmk(*args)
    except RuntimeError:
        return None


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------

def resolve_zone(zone_name):
    """Resolve zone name to ID."""
    data = cmk("list", "zones", f"name={zone_name}", "filter=id,name")
    for z in data.get("zone", []):
        if z["name"] == zone_name:
            return z["id"]
    raise RuntimeError(f"Zone '{zone_name}' not found")


def resolve_network_offering(name):
    """Resolve network offering name to ID."""
    data = cmk("list", "networkofferings", "filter=id,name")
    for no in data.get("networkoffering", []):
        if no["name"] == name:
            return no["id"]
    raise RuntimeError(f"Network offering '{name}' not found")


def resolve_service_offering(name):
    """Resolve service offering name to ID."""
    data = cmk("list", "serviceofferings", "filter=id,name")
    for so in data.get("serviceoffering", []):
        if so["name"] == name:
            return so["id"]
    raise RuntimeError(f"Service offering '{name}' not found")


def resolve_disk_offering(name):
    """Resolve disk offering name to ID."""
    data = cmk("list", "diskofferings", "filter=id,name")
    for do in data.get("diskoffering", []):
        if do["name"] == name:
            return do["id"]
    raise RuntimeError(f"Disk offering '{name}' not found")


def discover_template(zone_id):
    """Discover the Ubuntu 24.x template in the given zone."""
    data = cmk("list", "templates", "templatefilter=featured",
               "keyword=Ubuntu", f"zoneid={zone_id}", "filter=id,name,created")
    matches = []
    seen = set()
    for t in data.get("template", []):
        if TEMPLATE_REGEX.match(t["name"]) and t["id"] not in seen:
            seen.add(t["id"])
            matches.append(t)
    if not matches:
        raise RuntimeError("No Ubuntu template matching ^Ubuntu.*24.*$ found")
    best = sorted(matches, key=lambda t: t["created"], reverse=True)[0]
    print(f"  Template: {best['name']} ({best['id']})")
    return best["id"]


# ---------------------------------------------------------------------------
# Idempotency helpers
# ---------------------------------------------------------------------------

def find_network(name):
    """Find existing network by name, return ID or None."""
    data = cmk_quiet("list", "networks", "filter=id,name")
    if data:
        for n in data.get("network", []):
            if n["name"] == name:
                return n["id"]
    return None


def find_keypair(name):
    """Find existing SSH key pair by name."""
    data = cmk_quiet("list", "sshkeypairs", f"name={name}")
    return bool(data and data.get("sshkeypair"))


def find_vm(name):
    """Find existing VM by name, return ID or None."""
    data = cmk_quiet("list", "virtualmachines", f"name={name}",
                     "filter=id,name,state")
    if data:
        for vm in data.get("virtualmachine", []):
            if vm["name"] == name:
                return vm["id"]
    return None


def find_volume(name):
    """Find existing volume by name, return dict or None."""
    data = cmk_quiet("list", "volumes", f"name={name}", "type=DATADISK",
                     "filter=id,name,virtualmachineid,state")
    if data:
        for v in data.get("volume", []):
            if v["name"] == name:
                return v
    return None


def find_public_ips(network_id):
    """Find non-source-NAT public IPs associated with a network."""
    data = cmk_quiet("list", "publicipaddresses",
                     f"associatednetworkid={network_id}",
                     "filter=id,ipaddress,issourcenat")
    ips = []
    if data:
        for ip in data.get("publicipaddress", []):
            if not ip.get("issourcenat", False):
                ips.append(ip)
    return ips


def find_firewall_rules(ip_id):
    """Find firewall rules for an IP."""
    data = cmk_quiet("list", "firewallrules", f"ipaddressid={ip_id}",
                     "filter=id,startport,endport")
    return data.get("firewallrule", []) if data else []


def is_static_nat_enabled(ip_id):
    """Check if static NAT is already enabled for an IP."""
    data = cmk_quiet("list", "publicipaddresses", f"id={ip_id}",
                     "filter=id,isstaticnat,virtualmachineid")
    if data and data.get("publicipaddress"):
        return data["publicipaddress"][0].get("isstaticnat", False)
    return False


# ---------------------------------------------------------------------------
# Userdata helpers
# ---------------------------------------------------------------------------

def encode_userdata(script_path):
    """Read a userdata script and return its base64-encoded content."""
    with open(script_path, "r") as f:
        content = f.read()
    return base64.b64encode(content.encode()).decode()


# ---------------------------------------------------------------------------
# Resource creation helpers
# ---------------------------------------------------------------------------

def deploy_vm(name, offering_id, template_id, zone_id, net_id, keypair_name,
              userdata_path=None):
    """Deploy a VM or return existing one's ID.

    If userdata_path is provided and the file exists, the script is
    base64-encoded and passed as cloud-init userdata during deployment.
    """
    vm_id = find_vm(name)
    if vm_id:
        print(f"  Already exists: {name} ({vm_id})")
        return vm_id
    deploy_args = [
        "deploy", "virtualmachine",
        f"serviceofferingid={offering_id}",
        f"templateid={template_id}",
        f"zoneid={zone_id}",
        f"networkids={net_id}",
        f"keypair={keypair_name}",
        f"name={name}",
        f"displayname={name}",
    ]
    if userdata_path and os.path.exists(userdata_path):
        deploy_args.append(f"userdata={encode_userdata(userdata_path)}")
    data = cmk(*deploy_args)
    vm_id = data["virtualmachine"]["id"]
    print(f"  Created: {name} ({vm_id})")
    if userdata_path and os.path.exists(userdata_path):
        print(f"  Userdata: {os.path.basename(userdata_path)} (cloud-init)")
    return vm_id


def create_disk(disk_name, disk_offering_id, zone_id, size_gb, vm_id,
                network_name, desc):
    """Create, tag, and attach a data disk, or skip if it already exists."""
    vol = find_volume(disk_name)
    if vol:
        vol_id = vol["id"]
        print(f"  {desc}: already exists ({vol_id})")
        if not vol.get("virtualmachineid"):
            cmk("attach", "volume", f"id={vol_id}",
                f"virtualmachineid={vm_id}")
            print(f"    Attached to VM")
    else:
        data = cmk("create", "volume",
                    f"name={disk_name}",
                    f"diskofferingid={disk_offering_id}",
                    f"zoneid={zone_id}",
                    f"size={size_gb}")
        vol_id = data["volume"]["id"]
        print(f"  {desc}: created ({vol_id})")
        cmk("create", "tags",
            f"resourceids={vol_id}",
            "resourcetype=Volume",
            "tags[0].key=locaweb-ai-deploy-id",
            f"tags[0].value={network_name}")
        print(f"    Tagged with locaweb-ai-deploy-id={network_name}")
        cmk("attach", "volume", f"id={vol_id}",
            f"virtualmachineid={vm_id}")
        print(f"    Attached to VM")
    return vol_id


def create_snapshot_policy(vol_id, network_name, desc):
    """Create daily snapshot policy if one does not already exist."""
    existing = cmk_quiet("list", "snapshotpolicies", f"volumeid={vol_id}")
    if existing and existing.get("snapshotpolicy"):
        print(f"  {desc}: policy already exists")
    else:
        cmk("create", "snapshotpolicy",
            f"volumeid={vol_id}",
            "intervaltype=daily",
            f"schedule={SNAPSHOT_SCHEDULE}",
            f"maxsnaps={SNAPSHOT_MAX}",
            f"timezone={SNAPSHOT_TIMEZONE}",
            "zoneids=ZP01,ZP02",
            "tags[0].key=locaweb-ai-deploy-id",
            f"tags[0].value={network_name}")
        print(f"  {desc}: daily snapshot policy created")


def get_vm_internal_ip(vm_id):
    """Get the internal/private IP of a VM."""
    data = cmk("list", "virtualmachines", f"id={vm_id}", "filter=id,nic")
    return data["virtualmachine"][0]["nic"][0]["ipaddress"]


# ---------------------------------------------------------------------------
# Main provisioning logic
# ---------------------------------------------------------------------------

def provision(config, repo_name, unique_id, public_key):
    """Provision all infrastructure based on the validated config."""
    zone_name = config["zone"]
    web_plan = config["web_plan"]
    blob_disk_size_gb = config["blob_disk_size_gb"]
    workers_enabled = config["workers_enabled"]
    db_enabled = config["db_enabled"]

    network_name = f"{repo_name}-{unique_id}"
    keypair_name = f"{network_name}-key"
    web_vm_name = f"{network_name}-web"
    blob_disk_name = f"{network_name}-blob"

    results = {"network_name": network_name}

    # Count total public IPs needed
    total_ips = 1  # web always
    if workers_enabled:
        total_ips += config["workers_replicas"]
    if db_enabled:
        total_ips += 1

    print(f"\n{'='*60}")
    print(f"Provisioning: {network_name}")
    print(f"Zone: {zone_name}")
    print(f"Web: {web_plan} | Blob disk: {blob_disk_size_gb}GB")
    if workers_enabled:
        print(f"Workers: {config['workers_replicas']}x {config['workers_plan']}")
    if db_enabled:
        print(f"DB: {config['db_plan']} | DB disk: {config['db_disk_size_gb']}GB")
    print(f"Total public IPs needed: {total_ips}")
    print(f"{'='*60}\n")

    # --- Resolve all names to IDs ---
    print("Resolving infrastructure names...")
    zone_id = resolve_zone(zone_name)
    net_offering_id = resolve_network_offering(NETWORK_OFFERING_NAME)
    disk_offering_id = resolve_disk_offering(DISK_OFFERING_NAME)
    web_offering_id = resolve_service_offering(web_plan)
    template_id = discover_template(zone_id)

    worker_offering_id = None
    if workers_enabled:
        worker_offering_id = resolve_service_offering(config["workers_plan"])

    db_offering_id = None
    if db_enabled:
        db_offering_id = resolve_service_offering(config["db_plan"])

    print("  All names resolved.\n")

    # --- Network ---
    print("Creating isolated network...")
    net_id = find_network(network_name)
    if net_id:
        print(f"  Already exists: {net_id}")
    else:
        data = cmk("create", "network",
                    f"name={network_name}",
                    f"displaytext={network_name}",
                    f"networkofferingid={net_offering_id}",
                    f"zoneid={zone_id}")
        net_id = data["network"]["id"]
        print(f"  Created: {net_id}")
    results["network_id"] = net_id

    # --- SSH Key Pair ---
    print("\nRegistering SSH key pair...")
    if find_keypair(keypair_name):
        print(f"  Already exists: {keypair_name}")
    else:
        cmk("register", "sshkeypair",
            f"name={keypair_name}",
            f"publickey={public_key}")
        print(f"  Registered: {keypair_name}")
    results["keypair_name"] = keypair_name

    # --- Deploy VMs ---
    print("\nDeploying web VM...")
    web_vm_id = deploy_vm(web_vm_name, web_offering_id, template_id,
                          zone_id, net_id, keypair_name,
                          userdata_path=WEB_USERDATA)
    results["web_vm_id"] = web_vm_id

    worker_vm_ids = []
    if workers_enabled:
        num_workers = config["workers_replicas"]
        print(f"\nDeploying {num_workers} worker VM(s)...")
        for i in range(1, num_workers + 1):
            worker_name = f"{network_name}-worker-{i}"
            wid = deploy_vm(worker_name, worker_offering_id, template_id,
                            zone_id, net_id, keypair_name)
            worker_vm_ids.append(wid)
        results["worker_vm_ids"] = worker_vm_ids

    db_vm_id = None
    if db_enabled:
        db_vm_name = f"{network_name}-db"
        print("\nDeploying database VM...")
        db_vm_id = deploy_vm(db_vm_name, db_offering_id, template_id,
                             zone_id, net_id, keypair_name,
                             userdata_path=DB_USERDATA)
        results["db_vm_id"] = db_vm_id

    # --- Public IPs ---
    print("\nAcquiring public IPs...")
    existing_ips = find_public_ips(net_id)
    needed = total_ips - len(existing_ips)
    if needed > 0:
        for _ in range(needed):
            data = cmk("associate", "ipaddress", f"networkid={net_id}")
            existing_ips.append(data["ipaddress"])
        print(f"  Acquired {needed} new IP(s)")
    else:
        print(f"  Already have enough IPs ({len(existing_ips)})")

    # Assign IPs in order: web, workers (1..N), db
    idx = 0
    web_ip = existing_ips[idx]; idx += 1
    print(f"  Web IP: {web_ip['ipaddress']}")
    results["web_ip"] = web_ip["ipaddress"]
    results["web_ip_id"] = web_ip["id"]

    worker_ips = []
    if workers_enabled:
        for i in range(config["workers_replicas"]):
            wip = existing_ips[idx]; idx += 1
            worker_ips.append(wip)
            print(f"  Worker {i+1} IP: {wip['ipaddress']}")
        results["worker_ips"] = [ip["ipaddress"] for ip in worker_ips]

    db_ip = None
    if db_enabled:
        db_ip = existing_ips[idx]; idx += 1
        print(f"  DB IP: {db_ip['ipaddress']}")
        results["db_ip"] = db_ip["ipaddress"]
        results["db_ip_id"] = db_ip["id"]

    # --- Static NAT ---
    print("\nEnabling static NAT...")
    nat_assignments = [(web_ip, web_vm_id, "Web IP -> Web VM")]
    if workers_enabled:
        for i, (wip, wid) in enumerate(zip(worker_ips, worker_vm_ids), 1):
            nat_assignments.append((wip, wid, f"Worker {i} IP -> Worker {i} VM"))
    if db_enabled:
        nat_assignments.append((db_ip, db_vm_id, "DB IP -> DB VM"))

    for ip_obj, vm_id, desc in nat_assignments:
        if is_static_nat_enabled(ip_obj["id"]):
            print(f"  {desc}: already enabled")
        else:
            cmk("enable", "staticnat",
                f"ipaddressid={ip_obj['id']}",
                f"virtualmachineid={vm_id}")
            print(f"  {desc}: enabled")

    # --- Firewall Rules ---
    print("\nCreating firewall rules...")
    fw_rules = [
        (web_ip["id"], 22, 22, "SSH (web)"),
        (web_ip["id"], 80, 80, "HTTP (web)"),
        (web_ip["id"], 443, 443, "HTTPS (web)"),
    ]
    if workers_enabled:
        for i, wip in enumerate(worker_ips, 1):
            fw_rules.append((wip["id"], 22, 22, f"SSH (worker-{i})"))
    if db_enabled:
        fw_rules.append((db_ip["id"], 22, 22, "SSH (db)"))

    for ip_id, start, end, desc in fw_rules:
        existing = find_firewall_rules(ip_id)
        already = any(
            int(r.get("startport", 0)) == start and int(r.get("endport", 0)) == end
            for r in existing
        )
        if already:
            print(f"  {desc} ({start}-{end}): already exists")
        else:
            cmk("create", "firewallrule",
                f"ipaddressid={ip_id}", "protocol=TCP",
                f"startport={start}", f"endport={end}",
                "cidrlist=0.0.0.0/0")
            print(f"  {desc} ({start}-{end}): created")

    # --- Data Disks ---
    print("\nCreating data disks...")
    blob_vol_id = create_disk(blob_disk_name, disk_offering_id, zone_id,
                              blob_disk_size_gb, web_vm_id,
                              network_name, "Blob disk (web)")
    results["blob_volume_id"] = blob_vol_id

    if db_enabled:
        db_disk_name = f"{network_name}-dbdata"
        db_vol_id = create_disk(db_disk_name, disk_offering_id, zone_id,
                                config["db_disk_size_gb"], db_vm_id,
                                network_name, "DB disk (db)")
        results["db_volume_id"] = db_vol_id

    # --- Snapshot Policies ---
    print("\nCreating snapshot policies...")
    create_snapshot_policy(blob_vol_id, network_name, "Blob disk")
    if db_enabled:
        create_snapshot_policy(db_vol_id, network_name, "DB disk")

    # --- Internal IPs ---
    print("\nRetrieving internal IPs...")
    results["web_internal_ip"] = get_vm_internal_ip(web_vm_id)
    print(f"  Web: {results['web_internal_ip']}")

    if workers_enabled:
        results["worker_internal_ips"] = []
        for i, wid in enumerate(worker_vm_ids, 1):
            wip = get_vm_internal_ip(wid)
            results["worker_internal_ips"].append(wip)
            print(f"  Worker {i}: {wip}")

    if db_enabled:
        results["db_internal_ip"] = get_vm_internal_ip(db_vm_id)
        print(f"  DB: {results['db_internal_ip']}")

    # --- Summary ---
    print(f"\n{'='*60}")
    print("Provisioning complete!")
    print(f"{'='*60}")
    print(f"  Network:      {network_name} ({net_id})")
    print(f"  SSH Key Pair: {keypair_name}")
    print(f"  Web VM:       {web_vm_name} -> {web_ip['ipaddress']}")
    if workers_enabled:
        for i in range(config["workers_replicas"]):
            print(f"  Worker {i+1} VM:  {network_name}-worker-{i+1} -> {worker_ips[i]['ipaddress']}")
    if db_enabled:
        print(f"  DB VM:        {network_name}-db -> {db_ip['ipaddress']}")
    print(f"{'='*60}\n")

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Provision CloudStack infrastructure from a validated config")
    parser.add_argument("--repo-name", required=True,
                        help="Repository name")
    parser.add_argument("--unique-id", required=True,
                        help="Unique identifier (repository ID)")
    parser.add_argument("--config", required=True,
                        help="Path to validated JSON config file")
    parser.add_argument("--public-key", required=True,
                        help="Path to SSH public key file")
    parser.add_argument("--output", default=None,
                        help="Path to write JSON output (default: stdout)")
    args = parser.parse_args()

    with open(args.config) as f:
        config = json.load(f)

    with open(args.public_key) as f:
        public_key = f.read().strip()

    try:
        results = provision(config, repo_name=args.repo_name,
                            unique_id=args.unique_id, public_key=public_key)
        if args.output:
            with open(args.output, "w") as f:
                json.dump(results, f, indent=2)
            print(f"\nOutput written to {args.output}")
        else:
            json.dump(results, sys.stdout, indent=2)
            print()
    except RuntimeError as e:
        print(f"\nFATAL: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
