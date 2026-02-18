#!/usr/bin/env python3
"""
E2E test orchestrator for Locaweb CloudStack deployment.

Triggers the real deploy.yml workflow via `gh`, waits for completion,
downloads the provision-output artifact, then verifies the deployed
application works correctly (HTTP, SSH, disk mounts, env vars).

Environment variables:
  GH_TOKEN                       - GitHub token for gh CLI auth
  REPO_FULL                      - Full repository path (owner/name)
  REPO_NAME                      - Repository name
  REPO_ID                        - Repository ID (used for resource naming)
  ZONE                           - CloudStack zone (ZP01/ZP02)
  SCENARIO                       - Test scenario (complete/web-only/scale-up/scale-down/all)
  SSH_KEY_PATH                   - Path to SSH private key
  ROUTE_53_AWS_ACCESS_KEY_ID     - AWS access key for Route53 DNS management
  ROUTE_53_AWS_SECRET_ACCESS_KEY - AWS secret key for Route53 DNS management
  ROUTE_53_HOSTED_ZONE_ID        - Route53 hosted zone ID for kamal.giba.tech
"""
import datetime
import json
import os
import ssl
import subprocess
import sys
import time
import traceback
from http.client import HTTPConnection, HTTPSConnection
from urllib.parse import urlencode

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_FULL = os.environ.get("REPO_FULL", "")
REPO_NAME = os.environ.get("REPO_NAME", "")
REPO_ID = os.environ.get("REPO_ID", "")
ZONE = os.environ.get("ZONE", "ZP01")
SCENARIO = os.environ.get("SCENARIO", "all")
SSH_KEY_PATH = os.environ.get("SSH_KEY_PATH", "/tmp/ssh_key")
DEFAULT_ENV_NAME = "preview"


def make_network_name(env_name=DEFAULT_ENV_NAME):
    return f"{REPO_NAME}-{REPO_ID}-{env_name}"

RESULTS_PATH = "/tmp/e2e-test-results.json"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# Timeouts
WORKFLOW_POLL_INTERVAL = 15  # seconds between polls for new run
WORKFLOW_POLL_TIMEOUT = 120  # seconds to wait for new run to appear
WORKFLOW_WATCH_TIMEOUT = 2400  # 40 minutes max for a deploy workflow


# Route53 DNS settings
ROUTE_53_AWS_ACCESS_KEY_ID = os.environ.get("ROUTE_53_AWS_ACCESS_KEY_ID", "")
ROUTE_53_AWS_SECRET_ACCESS_KEY = os.environ.get("ROUTE_53_AWS_SECRET_ACCESS_KEY", "")
ROUTE_53_HOSTED_ZONE_ID = os.environ.get("ROUTE_53_HOSTED_ZONE_ID", "")
DNS_DOMAIN_SUFFIX = "kamal.giba.tech"


# ---------------------------------------------------------------------------
# Route53 DNS helper
# ---------------------------------------------------------------------------

def route53_upsert_a_record(domain, ip):
    """Create or update a Route53 A record. Returns True on success."""
    print(f"  Creating Route53 A record: {domain} -> {ip}")
    change_batch = json.dumps({
        "Changes": [{
            "Action": "UPSERT",
            "ResourceRecordSet": {
                "Name": domain,
                "Type": "A",
                "TTL": 60,
                "ResourceRecords": [{"Value": ip}],
            },
        }],
    })
    cmd = [
        "aws", "route53", "change-resource-record-sets",
        "--hosted-zone-id", ROUTE_53_HOSTED_ZONE_ID,
        "--change-batch", change_batch,
    ]
    env = os.environ.copy()
    env["AWS_ACCESS_KEY_ID"] = ROUTE_53_AWS_ACCESS_KEY_ID
    env["AWS_SECRET_ACCESS_KEY"] = ROUTE_53_AWS_SECRET_ACCESS_KEY
    env["AWS_SESSION_TOKEN"] = ""
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        print(f"  Route53 UPSERT failed: {result.stderr}")
        return False
    print(f"  Route53 A record created: {domain} -> {ip}")
    return True


def route53_delete_a_record(domain, ip):
    """Delete a Route53 A record. Non-fatal on failure."""
    print(f"  Deleting Route53 A record: {domain}")
    change_batch = json.dumps({
        "Changes": [{
            "Action": "DELETE",
            "ResourceRecordSet": {
                "Name": domain,
                "Type": "A",
                "TTL": 60,
                "ResourceRecords": [{"Value": ip}],
            },
        }],
    })
    cmd = [
        "aws", "route53", "change-resource-record-sets",
        "--hosted-zone-id", ROUTE_53_HOSTED_ZONE_ID,
        "--change-batch", change_batch,
    ]
    env = os.environ.copy()
    env["AWS_ACCESS_KEY_ID"] = ROUTE_53_AWS_ACCESS_KEY_ID
    env["AWS_SECRET_ACCESS_KEY"] = ROUTE_53_AWS_SECRET_ACCESS_KEY
    env["AWS_SESSION_TOKEN"] = ""
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        print(f"  Warning: Route53 DELETE failed: {result.stderr}")
    else:
        print(f"  Route53 A record deleted: {domain}")


def generate_test_domain():
    """Generate a timestamped test domain under kamal.giba.tech."""
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"test-{ts}.{DNS_DOMAIN_SUFFIX}"


# ---------------------------------------------------------------------------
# CloudMonkey helper
# ---------------------------------------------------------------------------

def cmk(*args):
    """Run a cmk command and return parsed JSON, or None on error."""
    cmd = ["cmk"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip():
        return json.loads(result.stdout)
    return None


def get_vm_offering_name(vm_id):
    """Query the current service offering name of a VM via cmk."""
    data = cmk("list", "virtualmachines", f"id={vm_id}",
               "filter=id,serviceofferingname")
    if data:
        for vm in data.get("virtualmachine", []):
            return vm.get("serviceofferingname")
    return None


# ---------------------------------------------------------------------------
# Workflow Triggering
# ---------------------------------------------------------------------------

def gh(*args, check=True):
    """Run a gh CLI command. Returns (returncode, stdout, stderr)."""
    cmd = ["gh"] + list(args)
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"gh command failed (rc={result.returncode}): {result.stderr.strip()}")
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def get_latest_run_id(workflow):
    """Get the ID of the most recent run for a workflow."""
    _, stdout, _ = gh(
        "run", "list",
        "--workflow", workflow,
        "--limit", "1",
        "--json", "databaseId",
        "-R", REPO_FULL,
    )
    runs = json.loads(stdout) if stdout else []
    if runs:
        return runs[0]["databaseId"]
    return 0


def trigger_workflow(workflow, inputs):
    """Trigger a workflow and wait for the new run to appear.

    Returns the run ID of the triggered workflow.
    """
    before_id = get_latest_run_id(workflow)
    print(f"  Latest run ID before trigger: {before_id}")

    # Build the gh workflow run command
    args = ["workflow", "run", workflow, "-R", REPO_FULL]
    for k, v in inputs.items():
        args.extend(["-f", f"{k}={v}"])

    gh(*args)
    print(f"  Triggered {workflow}, waiting for new run...")

    # Poll until a new run appears
    deadline = time.time() + WORKFLOW_POLL_TIMEOUT
    while time.time() < deadline:
        time.sleep(WORKFLOW_POLL_INTERVAL)
        current_id = get_latest_run_id(workflow)
        if current_id > before_id:
            print(f"  New run detected: {current_id}")
            return current_id
        print(f"  Polling... (latest={current_id})")

    raise RuntimeError(
        f"Timed out waiting for new {workflow} run after {WORKFLOW_POLL_TIMEOUT}s")


def wait_for_run(run_id):
    """Wait for a workflow run to complete. Raises on failure."""
    print(f"  Watching run {run_id} (timeout {WORKFLOW_WATCH_TIMEOUT}s)...")
    cmd = ["gh", "run", "watch", str(run_id),
           "--exit-status", "-R", REPO_FULL]
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True,
                            timeout=WORKFLOW_WATCH_TIMEOUT)
    rc, stdout, stderr = result.returncode, result.stdout.strip(), result.stderr.strip()
    if rc != 0:
        # Print the run URL for debugging
        gh("run", "view", str(run_id), "--web",
           "-R", REPO_FULL, check=False)
        raise RuntimeError(
            f"Workflow run {run_id} failed (rc={rc}): {stderr}")
    print(f"  Run {run_id} completed successfully")


def download_artifact(run_id, artifact_name, dest_dir="/tmp"):
    """Download a workflow artifact. Returns path to the downloaded directory."""
    out_dir = os.path.join(dest_dir, artifact_name)
    # Clean previous download so gh doesn't fail on existing files
    if os.path.isdir(out_dir):
        for f in os.listdir(out_dir):
            os.remove(os.path.join(out_dir, f))
    os.makedirs(out_dir, exist_ok=True)
    gh("run", "download", str(run_id),
       "-n", artifact_name,
       "-D", out_dir,
       "-R", REPO_FULL)
    print(f"  Downloaded artifact '{artifact_name}' to {out_dir}")
    return out_dir


def trigger_deploy(inputs):
    """Trigger deploy.yml, wait for completion, return provision output dict."""
    print("\n  --- Triggering deploy workflow ---")
    run_id = trigger_workflow("deploy.yml", inputs)
    wait_for_run(run_id)
    art_dir = download_artifact(run_id, "provision-output")
    output_path = os.path.join(art_dir, "provision-output.json")
    with open(output_path) as f:
        return json.load(f)


def trigger_teardown(env_name=DEFAULT_ENV_NAME):
    """Trigger teardown.yml and wait for completion."""
    print("\n  --- Triggering teardown workflow ---")
    run_id = trigger_workflow("teardown.yml", {
        "zone": ZONE,
        "env_name": env_name,
    })
    wait_for_run(run_id)
    print("  Teardown complete")


# ---------------------------------------------------------------------------
# SSH Verifier
# ---------------------------------------------------------------------------

class SSHVerifier:
    """SSH connectivity and remote command execution."""

    def __init__(self, key_path=SSH_KEY_PATH):
        self.key_path = key_path
        self.ssh_opts = [
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=10",
            "-o", "BatchMode=yes",
            "-i", self.key_path,
        ]

    def run_command(self, ip, command):
        """Run a remote command via SSH. Returns (rc, stdout, stderr)."""
        cmd = ["ssh"] + self.ssh_opts + [f"root@{ip}", command]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return result.returncode, result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            return 1, "", "SSH command timed out"

    def wait_for_ssh(self, ip, timeout=180):
        """Poll SSH every 10s until it responds or timeout is reached."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            rc, _, _ = self.run_command(ip, "true")
            if rc == 0:
                return True
            time.sleep(10)
        return False

    def verify_mount_point(self, ip, path, timeout=120):
        """Poll mountpoint with retry (cloud-init may still be running)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            rc, _, _ = self.run_command(ip, f"mountpoint -q {path}")
            if rc == 0:
                return True
            time.sleep(10)
        return False

    def verify_disk_writable(self, ip, path):
        """Write and read a test file to verify disk persistence."""
        test_file = f"{path}/.e2e-test-{int(time.time())}"
        rc, _, _ = self.run_command(
            ip, f"echo e2e-ok > {test_file} && cat {test_file} && rm -f {test_file}")
        return rc == 0

    def find_app_container(self, ip):
        """Find the running app container name on a host."""
        rc, stdout, _ = self.run_command(
            ip, "docker ps --format '{{.Names}}' | grep -v kamal-proxy | head -1")
        if rc == 0 and stdout:
            return stdout.split('\n')[0].strip()
        return None

    def get_container_env(self, ip, container, var_name):
        """Get an environment variable value from inside a container."""
        rc, stdout, _ = self.run_command(
            ip, f"docker exec {container} printenv {var_name}")
        if rc == 0:
            return stdout
        return None

    def get_pg_setting(self, ip, container, setting):
        """Get a PostgreSQL setting value from inside a running container.

        Runs `SHOW <setting>` via psql and returns the trimmed value,
        or None on failure.  Uses hardcoded postgres user/db since inline with the supabase/postgres image.
        """
        rc, stdout, _ = self.run_command(
            ip,
            f"docker exec {container} "
            f"psql -U postgres -d postgres "
            f"-At -c \"SHOW {setting};\"")
        if rc == 0 and stdout.strip():
            return stdout.strip()
        return None

    def get_block_device_size(self, ip, mount_path):
        """Get the raw block device size in bytes for a given mount point.

        Uses blockdev --getsize64 on the device backing the mount.
        Returns the size in bytes, or None on failure.
        """
        rc, stdout, _ = self.run_command(
            ip,
            f"blockdev --getsize64 /dev/$(lsblk -rno NAME,MOUNTPOINT "
            f"| grep '{mount_path}$' | awk '{{print $1}}')")
        if rc == 0 and stdout.strip().isdigit():
            return int(stdout.strip())
        return None

    def verify_auto_upgrades_enabled(self, ip):
        """Check that /etc/apt/apt.conf.d/20auto-upgrades has the right content."""
        rc, stdout, _ = self.run_command(
            ip, "cat /etc/apt/apt.conf.d/20auto-upgrades")
        if rc != 0:
            return False
        return ('Update-Package-Lists "1"' in stdout
                and 'Unattended-Upgrade "1"' in stdout)

    def verify_automatic_reboot(self, ip, expected_time):
        """Check that 52-automatic-reboots exists with correct reboot settings."""
        rc, stdout, _ = self.run_command(
            ip, "cat /etc/apt/apt.conf.d/52-automatic-reboots")
        if rc != 0:
            return False
        return ('Automatic-Reboot "true"' in stdout
                and f'Automatic-Reboot-Time "{expected_time}"' in stdout)

    def verify_no_automatic_reboot(self, ip):
        """Check that 52-automatic-reboots does NOT exist."""
        rc, _, _ = self.run_command(
            ip, "test ! -f /etc/apt/apt.conf.d/52-automatic-reboots")
        return rc == 0

    def verify_fail2ban(self, ip):
        """Check that fail2ban is running with the expected sshd jail settings.

        Returns a dict with keys: active, bantime, findtime, maxretry.
        Values are None on failure.
        """
        result = {"active": False, "bantime": None, "findtime": None,
                  "maxretry": None}
        # Check sshd jail is active
        rc, stdout, _ = self.run_command(ip, "fail2ban-client status sshd")
        if rc != 0:
            return result
        result["active"] = True
        # Read effective settings
        for setting in ("bantime", "findtime", "maxretry"):
            rc, stdout, _ = self.run_command(
                ip, f"fail2ban-client get sshd {setting}")
            if rc == 0 and stdout.strip().lstrip('-').isdigit():
                result[setting] = int(stdout.strip())
        return result


# ---------------------------------------------------------------------------
# HTTP Verifier
# ---------------------------------------------------------------------------

class HTTPVerifier:
    """HTTP request verification against the deployed application.

    When domain is provided, uses HTTPS with certificate verification.
    Otherwise, uses plain HTTP with nip.io Host header.
    """

    def __init__(self, ip, domain=None):
        self.ip = ip
        self.domain = domain
        self.host = domain if domain else f"{ip}.nip.io"
        self.use_https = bool(domain)

    def _connect(self, timeout=10):
        """Create an HTTP(S) connection."""
        if self.use_https:
            ctx = ssl.create_default_context()
            return HTTPSConnection(self.domain, 443, timeout=timeout, context=ctx)
        return HTTPConnection(self.ip, 80, timeout=timeout)

    def get(self, path="/", timeout=10):
        """HTTP(S) GET. Returns (status_code, body)."""
        try:
            conn = self._connect(timeout)
            conn.request("GET", path, headers={"Host": self.host})
            resp = conn.getresponse()
            body = resp.read().decode("utf-8", errors="replace")
            status = resp.status
            conn.close()
            return status, body
        except Exception as e:
            return 0, str(e)

    def post_form(self, path, data, timeout=10):
        """HTTP(S) POST with form-encoded data. Returns (status_code, body)."""
        try:
            encoded = urlencode(data)
            conn = self._connect(timeout)
            conn.request("POST", path,
                         body=encoded,
                         headers={
                             "Host": self.host,
                             "Content-Type": "application/x-www-form-urlencoded",
                         })
            resp = conn.getresponse()
            body = resp.read().decode("utf-8", errors="replace")
            status = resp.status
            conn.close()
            return status, body
        except Exception as e:
            return 0, str(e)

    def post_multipart(self, path, filename, content, timeout=10):
        """HTTP(S) POST multipart file upload. Returns (status_code, body)."""
        boundary = "----E2ETestBoundary"
        body_parts = [
            f"--{boundary}",
            f'Content-Disposition: form-data; name="file"; filename="{filename}"',
            "Content-Type: application/octet-stream",
            "",
            content,
            f"--{boundary}--",
            "",
        ]
        body = "\r\n".join(body_parts)
        try:
            conn = self._connect(timeout)
            conn.request("POST", path,
                         body=body.encode("utf-8"),
                         headers={
                             "Host": self.host,
                             "Content-Type": f"multipart/form-data; boundary={boundary}",
                         })
            resp = conn.getresponse()
            resp_body = resp.read().decode("utf-8", errors="replace")
            status = resp.status
            conn.close()
            return status, resp_body
        except Exception as e:
            return 0, str(e)

    def wait_for_healthy(self, path="/up", timeout=300):
        """Poll the health endpoint until 200 or timeout."""
        deadline = time.time() + timeout
        last_status = 0
        while time.time() < deadline:
            status, _ = self.get(path, timeout=5)
            last_status = status
            if status == 200:
                return True
            time.sleep(10)
        print(f"    Health check timed out (last status: {last_status})")
        return False

    def get_certificate_info(self):
        """Get the TLS certificate info from the server. Returns dict or None."""
        try:
            ctx = ssl.create_default_context()
            conn = ctx.wrap_socket(
                __import__('socket').create_connection((self.domain, 443), timeout=10),
                server_hostname=self.domain,
            )
            cert = conn.getpeercert()
            conn.close()
            return cert
        except Exception as e:
            print(f"    Certificate check failed: {e}")
            return None


# ---------------------------------------------------------------------------
# Test Scenario (same pattern as test_infrastructure.py)
# ---------------------------------------------------------------------------

class TestScenario:
    """Tracks assertions and duration for a single test scenario."""

    def __init__(self, name):
        self.name = name
        self.assertions = []
        self.status = "PASS"
        self.start_time = None
        self.duration = 0

    def __enter__(self):
        self.start_time = time.time()
        print(f"\n{'=' * 60}")
        print(f"SCENARIO: {self.name}")
        print(f"{'=' * 60}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.duration = time.time() - self.start_time
        if exc_type:
            self.status = "FAIL"
            self.assertions.append({
                "message": f"Exception: {exc_val}",
                "passed": False,
            })
            print(f"  [FAIL] Exception: {exc_val}")
            traceback.print_exception(exc_type, exc_val, exc_tb)
        passed = sum(1 for a in self.assertions if a["passed"])
        failed = sum(1 for a in self.assertions if not a["passed"])
        print(f"\n  Result: [{self.status}] {passed} passed, {failed} failed ({self.duration:.0f}s)")
        return True  # suppress exceptions so suite continues

    def assert_true(self, condition, message):
        passed = bool(condition)
        self.assertions.append({"message": message, "passed": passed})
        tag = "PASS" if passed else "FAIL"
        print(f"  [{tag}] {message}")
        if not passed:
            self.status = "FAIL"
        return passed

    def assert_equal(self, actual, expected, message):
        passed = actual == expected
        full_msg = f"{message} (expected={expected}, actual={actual})"
        self.assertions.append({"message": full_msg, "passed": passed})
        tag = "PASS" if passed else "FAIL"
        print(f"  [{tag}] {full_msg}")
        if not passed:
            self.status = "FAIL"
        return passed

    def assert_contains(self, haystack, needle, message):
        passed = needle in haystack
        self.assertions.append({"message": message, "passed": passed})
        tag = "PASS" if passed else "FAIL"
        print(f"  [{tag}] {message}")
        if not passed:
            self.status = "FAIL"
            # Print a snippet for debugging
            if len(haystack) > 500:
                print(f"    (body truncated to 500 chars): {haystack[:500]}")
            else:
                print(f"    (body): {haystack}")
        return passed

    def assert_not_contains(self, haystack, needle, message):
        passed = needle not in haystack
        self.assertions.append({"message": message, "passed": passed})
        tag = "PASS" if passed else "FAIL"
        print(f"  [{tag}] {message}")
        if not passed:
            self.status = "FAIL"
        return passed


# ---------------------------------------------------------------------------
# E2E Test Runner
# ---------------------------------------------------------------------------

class E2ETestRunner:
    """Orchestrates all E2E test scenarios."""

    def __init__(self):
        self.scenarios = []
        self.ssh = SSHVerifier()

    def save_results(self):
        """Write test results JSON."""
        total_pass = sum(
            sum(1 for a in s.assertions if a["passed"])
            for s in self.scenarios
        )
        total_fail = sum(
            sum(1 for a in s.assertions if not a["passed"])
            for s in self.scenarios
        )
        total_duration = sum(s.duration for s in self.scenarios)
        results = {
            "scenarios": [
                {
                    "name": s.name,
                    "status": s.status,
                    "duration": s.duration,
                    "assertions": s.assertions,
                }
                for s in self.scenarios
            ],
            "total_pass": total_pass,
            "total_fail": total_fail,
            "total_duration": total_duration,
        }
        with open(RESULTS_PATH, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults written to {RESULTS_PATH}")
        return total_fail == 0

    def run(self):
        """Run the selected scenario(s)."""
        scenario_map = {
            "complete": [self._scenario_complete],
            "web-only": [self._scenario_web_only],
            "scale-up": [self._scenario_scale_up],
            "scale-down": [self._scenario_scale_down],
            "all": [
                self._scenario_complete,
                self._scenario_web_only,
                self._scenario_scale_up,
                self._scenario_scale_down,
            ],
        }

        runners = scenario_map.get(SCENARIO, [self._scenario_complete])
        for runner in runners:
            runner()

        return self.save_results()

    # ------------------------------------------------------------------
    # Scenario: Complete (web + workers + db)
    # ------------------------------------------------------------------

    def _scenario_complete(self):
        s = TestScenario("Complete Deploy (web + workers + db)")
        with s:
            # Deploy
            output = trigger_deploy({
                "zone": ZONE,
                "env_name": DEFAULT_ENV_NAME,
                "workers_enabled": "true",
                "workers_replicas": "1",
                "db_enabled": "true",
                "automatic_reboot": "true",
                "automatic_reboot_time_utc": "03:30",
            })

            web_ip = output.get("web_ip", "")
            worker_ips = output.get("worker_ips", [])
            db_ip = output.get("db_ip", "")

            s.assert_true(web_ip, "Deploy produced web_ip")
            s.assert_true(len(worker_ips) >= 1, "Deploy produced worker_ips")
            s.assert_true(db_ip, "Deploy produced db_ip")

            if not web_ip:
                self.scenarios.append(s)
                return

            # HTTP: Health check
            http = HTTPVerifier(web_ip)
            s.assert_true(
                http.wait_for_healthy("/up", timeout=300),
                "HTTP /up returns 200")

            # HTTP: Index page loads
            status, body = http.get("/")
            s.assert_equal(status, 200, "HTTP GET / returns 200")

            # HTTP: Env vars visible
            s.assert_not_contains(body, "not set",
                                  "Env vars are set (no 'not set' on page)")

            # HTTP: DB is connected (no "Database not configured" message)
            s.assert_not_contains(body, "Database not configured",
                                  "Database is configured")
            s.assert_not_contains(body, "Database unavailable",
                                  "Database is reachable")

            # HTTP: Post a note
            test_note = f"e2e-test-note-{int(time.time())}"
            status, _ = http.post_form("/notes", {"content": test_note})
            s.assert_true(status in (200, 302),
                          f"POST /notes returns 200 or 302 (got {status})")

            # HTTP: Verify note appears
            status, body = http.get("/")
            s.assert_contains(body, test_note,
                              "Test note appears on page after POST")

            # HTTP: Upload a file
            test_filename = f"e2e-test-{int(time.time())}.txt"
            status, _ = http.post_multipart(
                "/upload", test_filename, "e2e test content")
            s.assert_true(status in (200, 302),
                          f"POST /upload returns 200 or 302 (got {status})")

            # HTTP: Verify file appears
            status, body = http.get("/")
            s.assert_contains(body, test_filename,
                              "Uploaded file appears on page")

            # SSH: Web VM - blob mount + implicit disk size (default 20GB)
            s.assert_true(
                self.ssh.wait_for_ssh(web_ip, timeout=60),
                "SSH to web VM: reachable")
            s.assert_true(
                self.ssh.verify_mount_point(web_ip, "/data/blobs"),
                "SSH to web VM: /data/blobs mounted")
            s.assert_true(
                self.ssh.verify_disk_writable(web_ip, "/data/blobs"),
                "SSH to web VM: /data/blobs writable")
            blob_size = self.ssh.get_block_device_size(web_ip, "/data/blobs")
            s.assert_equal(blob_size, 20 * 1024**3,
                           "Blob disk implicit default size is 20GB")

            # SSH: DB VM - db mount + implicit disk size (default 20GB)
            s.assert_true(
                self.ssh.wait_for_ssh(db_ip, timeout=60),
                "SSH to DB VM: reachable")
            s.assert_true(
                self.ssh.verify_mount_point(db_ip, "/data/db"),
                "SSH to DB VM: /data/db mounted")
            db_size = self.ssh.get_block_device_size(db_ip, "/data/db")
            s.assert_equal(db_size, 20 * 1024**3,
                           "DB disk implicit default size is 20GB")

            # SSH: Worker VM - env vars via docker
            for i, wip in enumerate(worker_ips, 1):
                s.assert_true(
                    self.ssh.wait_for_ssh(wip, timeout=60),
                    f"SSH to Worker-{i}: reachable")
                container = self.ssh.find_app_container(wip)
                s.assert_true(container,
                              f"SSH to Worker-{i}: app container found")
                if container:
                    my_var = self.ssh.get_container_env(wip, container, "MY_VAR")
                    s.assert_true(my_var is not None and my_var != "",
                                  f"SSH to Worker-{i}: MY_VAR is set")
                    my_secret = self.ssh.get_container_env(wip, container, "MY_SECRET")
                    s.assert_true(my_secret is not None and my_secret != "",
                                  f"SSH to Worker-{i}: MY_SECRET is set")

            # Unattended upgrades: all VMs should have auto-upgrades + reboot at 03:30
            all_ips = [("web", web_ip)] + [(f"worker-{i}", w) for i, w in enumerate(worker_ips, 1)] + [("db", db_ip)]
            for label, ip in all_ips:
                if ip:
                    s.assert_true(
                        self.ssh.verify_auto_upgrades_enabled(ip),
                        f"Unattended upgrades enabled on {label}")
                    s.assert_true(
                        self.ssh.verify_automatic_reboot(ip, "03:30"),
                        f"Automatic reboot at 03:30 on {label}")

            # fail2ban: all VMs should have sshd jail with hardened settings
            for label, ip in all_ips:
                if ip:
                    f2b = self.ssh.verify_fail2ban(ip)
                    s.assert_true(f2b["active"],
                                  f"fail2ban sshd jail active on {label}")
                    s.assert_equal(f2b["maxretry"], 3,
                                   f"fail2ban maxretry=3 on {label}")
                    s.assert_equal(f2b["bantime"], 3600,
                                   f"fail2ban bantime=3600 on {label}")
                    s.assert_equal(f2b["findtime"], 600,
                                   f"fail2ban findtime=600 on {label}")

            # Teardown
            trigger_teardown(DEFAULT_ENV_NAME)

        self.scenarios.append(s)

    # ------------------------------------------------------------------
    # Scenario: Web-only (no workers, no db)
    # ------------------------------------------------------------------

    def _scenario_web_only(self):
        s = TestScenario("Web-Only Deploy (custom domain + SSL)")
        domain = generate_test_domain()
        domain_ip = None
        with s:
            # Deploy with custom domain (no workers, no db), reboot disabled
            output = trigger_deploy({
                "zone": ZONE,
                "env_name": DEFAULT_ENV_NAME,
                "domain": domain,
                "automatic_reboot": "false",
            })

            web_ip = output.get("web_ip", "")
            domain_ip = web_ip  # save for cleanup
            s.assert_true(web_ip, "Deploy produced web_ip")
            s.assert_true("worker_ips" not in output or output["worker_ips"] == [],
                          "No worker IPs in output")
            s.assert_true("db_ip" not in output or output["db_ip"] == "",
                          "No DB IP in output")

            if not web_ip:
                self.scenarios.append(s)
                return

            # Create Route53 A record pointing domain to web IP
            s.assert_true(
                route53_upsert_a_record(domain, web_ip),
                f"Route53 A record created: {domain} -> {web_ip}")

            # Wait for DNS propagation (short TTL=60, but give it a moment)
            print(f"  Waiting 15s for DNS propagation...")
            time.sleep(15)

            # HTTPS: Health check via domain
            http = HTTPVerifier(web_ip, domain=domain)
            s.assert_true(
                http.wait_for_healthy("/up", timeout=300),
                "HTTPS /up returns 200 (no DB mode)")

            # HTTPS: Index page
            status, body = http.get("/")
            s.assert_equal(status, 200, "HTTPS GET / returns 200")

            # HTTPS: Shows "Database not configured"
            s.assert_contains(body, "Database not configured",
                              "Page shows 'Database not configured'")

            # HTTPS: Env vars still visible
            s.assert_not_contains(body, "not set",
                                  "Env vars are set (no 'not set' on page)")

            # HTTPS: Upload still works
            test_filename = f"e2e-webonly-{int(time.time())}.txt"
            status, _ = http.post_multipart(
                "/upload", test_filename, "web-only test")
            s.assert_true(status in (200, 302),
                          f"POST /upload returns 200 or 302 (got {status})")

            status, body = http.get("/")
            s.assert_contains(body, test_filename,
                              "Uploaded file appears on page")

            # TLS: Verify certificate is valid and issued for the domain
            cert = http.get_certificate_info()
            s.assert_true(cert is not None,
                          "TLS certificate retrieved successfully")
            if cert:
                # Check subject matches domain
                san = cert.get("subjectAltName", [])
                san_names = [v for t, v in san if t == "DNS"]
                s.assert_true(
                    domain in san_names,
                    f"Certificate SAN contains {domain}")
                # Check issuer is Let's Encrypt
                issuer = dict(x[0] for x in cert.get("issuer", []))
                s.assert_equal(
                    issuer.get("organizationName", ""), "Let's Encrypt",
                    "Certificate issued by Let's Encrypt")

            # SSH: Web VM - blob mount
            s.assert_true(
                self.ssh.wait_for_ssh(web_ip, timeout=60),
                "SSH to web VM: reachable")
            s.assert_true(
                self.ssh.verify_mount_point(web_ip, "/data/blobs"),
                "SSH to web VM: /data/blobs mounted")

            # Unattended upgrades: auto-upgrades present, but no automatic reboot
            s.assert_true(
                self.ssh.verify_auto_upgrades_enabled(web_ip),
                "Unattended upgrades enabled on web")
            s.assert_true(
                self.ssh.verify_no_automatic_reboot(web_ip),
                "No automatic reboot file on web (reboot disabled)")

            # fail2ban: web VM should have sshd jail with hardened settings
            f2b = self.ssh.verify_fail2ban(web_ip)
            s.assert_true(f2b["active"],
                          "fail2ban sshd jail active on web")
            s.assert_equal(f2b["maxretry"], 3,
                           "fail2ban maxretry=3 on web")
            s.assert_equal(f2b["bantime"], 3600,
                           "fail2ban bantime=3600 on web")
            s.assert_equal(f2b["findtime"], 600,
                           "fail2ban findtime=600 on web")

            # Teardown
            trigger_teardown(DEFAULT_ENV_NAME)

        # Clean up DNS record (outside the with block so it runs even on failure)
        if domain_ip:
            route53_delete_a_record(domain, domain_ip)

        self.scenarios.append(s)

    # ------------------------------------------------------------------
    # Scenario: Scale Up (1 worker -> 3 workers)
    # ------------------------------------------------------------------

    def _scenario_scale_up(self):
        s = TestScenario("Scale Up Workers (1 -> 3) + Offerings & Disks")
        with s:
            # Deploy with 1 worker, small plans, smaller disks
            output = trigger_deploy({
                "zone": ZONE,
                "env_name": "e2etest",
                "web_plan": "small",
                "workers_enabled": "true",
                "workers_replicas": "1",
                "workers_plan": "small",
                "db_enabled": "true",
                "db_plan": "small",
                "blob_disk_size_gb": "25",
                "db_disk_size_gb": "20",
            })

            web_ip = output.get("web_ip", "")
            db_ip = output.get("db_ip", "")
            worker_ips = output.get("worker_ips", [])
            s.assert_true(web_ip, "Deploy produced web_ip")
            s.assert_equal(len(worker_ips), 1,
                           "Initial deploy has 1 worker")

            # Verify initial deploy works
            http = HTTPVerifier(web_ip)
            s.assert_true(
                http.wait_for_healthy("/up", timeout=300),
                "HTTP /up returns 200 (initial deploy)")

            # Verify initial disk sizes
            s.assert_true(
                self.ssh.wait_for_ssh(web_ip, timeout=60),
                "SSH to web VM: reachable")
            s.assert_true(
                self.ssh.verify_mount_point(web_ip, "/data/blobs"),
                "SSH to web VM: /data/blobs mounted")
            blob_size = self.ssh.get_block_device_size(web_ip, "/data/blobs")
            s.assert_equal(blob_size, 25 * 1024**3,
                           "Blob disk initial size is 25GB")

            if db_ip:
                s.assert_true(
                    self.ssh.wait_for_ssh(db_ip, timeout=60),
                    "SSH to DB VM: reachable")
                s.assert_true(
                    self.ssh.verify_mount_point(db_ip, "/data/db"),
                    "SSH to DB VM: /data/db mounted")
                db_size = self.ssh.get_block_device_size(db_ip, "/data/db")
                s.assert_equal(db_size, 20 * 1024**3,
                               "DB disk initial size is 20GB")

                # Verify PG tuning for 'small' plan (2 GiB RAM)
                db_container = f"{REPO_NAME}-db"
                sb = self.ssh.get_pg_setting(db_ip, db_container, "shared_buffers")
                s.assert_equal(sb, "512MB",
                               "PG shared_buffers is 512MB (small plan)")
                ecs = self.ssh.get_pg_setting(db_ip, db_container, "effective_cache_size")
                s.assert_equal(ecs, "1536MB",
                               "PG effective_cache_size is 1536MB (small plan)")
                wm = self.ssh.get_pg_setting(db_ip, db_container, "work_mem")
                s.assert_equal(wm, "5MB",
                               "PG work_mem is 5MB (small plan)")
                mwm = self.ssh.get_pg_setting(db_ip, db_container, "maintenance_work_mem")
                s.assert_equal(mwm, "128MB",
                               "PG maintenance_work_mem is 128MB (small plan)")
                mc = self.ssh.get_pg_setting(db_ip, db_container, "max_connections")
                s.assert_equal(mc, "100",
                               "PG max_connections is 100 (small plan)")

            # Verify the single worker has env vars
            if worker_ips:
                wip = worker_ips[0]
                self.ssh.wait_for_ssh(wip, timeout=60)
                container = self.ssh.find_app_container(wip)
                s.assert_true(container,
                              "Initial worker: app container found")

            # Verify initial offerings are "small" before scale-up
            web_vm_id = output.get("web_vm_id", "")
            worker_vm_ids = output.get("worker_vm_ids", [])
            db_vm_id = output.get("db_vm_id", "")

            for label, vm_id in [("Web", web_vm_id),
                                 ("Worker-1", worker_vm_ids[0] if worker_vm_ids else ""),
                                 ("DB", db_vm_id)]:
                if vm_id:
                    before = get_vm_offering_name(vm_id)
                    print(f"    {label}: existing offer detected as: {before}")
                    s.assert_equal(before, "small",
                                   f"{label} offering is 'small' before scale")
                    print(f"    {label}: offer used in next API call will be: medium")

            # Scale up: 3 workers, medium plans, larger disks
            output2 = trigger_deploy({
                "zone": ZONE,
                "env_name": "e2etest",
                "web_plan": "medium",
                "workers_enabled": "true",
                "workers_replicas": "3",
                "workers_plan": "medium",
                "db_enabled": "true",
                "db_plan": "medium",
                "blob_disk_size_gb": "35",
                "db_disk_size_gb": "30",
            })

            worker_ips2 = output2.get("worker_ips", [])
            s.assert_equal(len(worker_ips2), 3,
                           "Scaled deploy has 3 workers")

            # Verify all 3 workers have app + env vars
            for i, wip in enumerate(worker_ips2, 1):
                s.assert_true(
                    self.ssh.wait_for_ssh(wip, timeout=120),
                    f"SSH to Worker-{i} (scaled): reachable")
                container = self.ssh.find_app_container(wip)
                s.assert_true(container,
                              f"Worker-{i} (scaled): app container found")
                if container:
                    my_var = self.ssh.get_container_env(wip, container, "MY_VAR")
                    s.assert_true(my_var is not None and my_var != "",
                                  f"Worker-{i} (scaled): MY_VAR is set")

            # Verify offerings changed to "medium" after scale-up
            for label, vm_id in [("Web", web_vm_id),
                                 ("Worker-1", worker_vm_ids[0] if worker_vm_ids else ""),
                                 ("DB", db_vm_id)]:
                if vm_id:
                    after = get_vm_offering_name(vm_id)
                    s.assert_equal(after, "medium",
                                   f"{label} offering is 'medium' after scale")
                    print(f"    {label}: API call succeeded (now: {after})")

            # Verify disk sizes grew after scale-up
            web_ip2 = output2.get("web_ip", web_ip)
            s.assert_true(
                self.ssh.wait_for_ssh(web_ip2, timeout=60),
                "SSH to web VM after scale: reachable")
            blob_size2 = self.ssh.get_block_device_size(web_ip2, "/data/blobs")
            s.assert_equal(blob_size2, 35 * 1024**3,
                           "Blob disk grew to 35GB after scale")

            db_ip2 = output2.get("db_ip", db_ip)
            if db_ip2:
                s.assert_true(
                    self.ssh.wait_for_ssh(db_ip2, timeout=60),
                    "SSH to DB VM after scale: reachable")
                db_size2 = self.ssh.get_block_device_size(db_ip2, "/data/db")
                s.assert_equal(db_size2, 30 * 1024**3,
                               "DB disk grew to 30GB after scale")

                # Verify PG tuning changed to 'medium' plan (4 GiB RAM)
                db_container = f"{REPO_NAME}-db"
                sb = self.ssh.get_pg_setting(db_ip2, db_container, "shared_buffers")
                s.assert_equal(sb, "1GB",
                               "PG shared_buffers is 1GB (medium plan)")
                ecs = self.ssh.get_pg_setting(db_ip2, db_container, "effective_cache_size")
                s.assert_equal(ecs, "3GB",
                               "PG effective_cache_size is 3GB (medium plan)")
                wm = self.ssh.get_pg_setting(db_ip2, db_container, "work_mem")
                s.assert_equal(wm, "10MB",
                               "PG work_mem is 10MB (medium plan)")
                mwm = self.ssh.get_pg_setting(db_ip2, db_container, "maintenance_work_mem")
                s.assert_equal(mwm, "256MB",
                               "PG maintenance_work_mem is 256MB (medium plan)")
                mc = self.ssh.get_pg_setting(db_ip2, db_container, "max_connections")
                s.assert_equal(mc, "100",
                               "PG max_connections is 100 (medium plan)")

            # App still works after scale
            http2 = HTTPVerifier(web_ip2)
            s.assert_true(
                http2.wait_for_healthy("/up", timeout=120),
                "HTTP /up returns 200 (after scale up)")

            # Unattended upgrades: defaults (reboot=true, time=05:00) on all VMs after scale
            all_ips_scaled = (
                [("web", web_ip2)]
                + [(f"worker-{i}", w) for i, w in enumerate(worker_ips2, 1)]
                + ([("db", db_ip2)] if db_ip2 else [])
            )
            for label, ip in all_ips_scaled:
                s.assert_true(
                    self.ssh.verify_auto_upgrades_enabled(ip),
                    f"Unattended upgrades enabled on {label} (after scale)")
                s.assert_true(
                    self.ssh.verify_automatic_reboot(ip, "05:00"),
                    f"Automatic reboot at 05:00 on {label} (after scale)")

            # Teardown
            trigger_teardown("e2etest")

        self.scenarios.append(s)

    # ------------------------------------------------------------------
    # Scenario: Scale Down (3 workers -> 1 worker)
    # ------------------------------------------------------------------

    def _scenario_scale_down(self):
        s = TestScenario("Scale Down Workers (3 -> 1)")
        with s:
            # Deploy with 3 workers
            output = trigger_deploy({
                "zone": ZONE,
                "env_name": DEFAULT_ENV_NAME,
                "workers_enabled": "true",
                "workers_replicas": "3",
                "db_enabled": "true",
            })

            web_ip = output.get("web_ip", "")
            worker_ips = output.get("worker_ips", [])
            s.assert_true(web_ip, "Deploy produced web_ip")
            s.assert_equal(len(worker_ips), 3,
                           "Initial deploy has 3 workers")

            # Verify initial
            http = HTTPVerifier(web_ip)
            s.assert_true(
                http.wait_for_healthy("/up", timeout=300),
                "HTTP /up returns 200 (initial deploy)")

            # Verify all 3 workers
            for i, wip in enumerate(worker_ips, 1):
                self.ssh.wait_for_ssh(wip, timeout=120)
                container = self.ssh.find_app_container(wip)
                s.assert_true(container,
                              f"Worker-{i} (initial): app container found")

            # Scale down to 1 worker
            output2 = trigger_deploy({
                "zone": ZONE,
                "env_name": DEFAULT_ENV_NAME,
                "workers_enabled": "true",
                "workers_replicas": "1",
                "db_enabled": "true",
            })

            worker_ips2 = output2.get("worker_ips", [])
            s.assert_equal(len(worker_ips2), 1,
                           "Scaled deploy has 1 worker")

            # Verify remaining worker has app + env vars
            if worker_ips2:
                wip = worker_ips2[0]
                s.assert_true(
                    self.ssh.wait_for_ssh(wip, timeout=120),
                    "SSH to remaining worker: reachable")
                container = self.ssh.find_app_container(wip)
                s.assert_true(container,
                              "Remaining worker: app container found")
                if container:
                    my_var = self.ssh.get_container_env(wip, container, "MY_VAR")
                    s.assert_true(my_var is not None and my_var != "",
                                  "Remaining worker: MY_VAR is set")

            # App still works after scale
            http2 = HTTPVerifier(output2.get("web_ip", web_ip))
            s.assert_true(
                http2.wait_for_healthy("/up", timeout=120),
                "HTTP /up returns 200 (after scale down)")

            # Teardown
            trigger_teardown(DEFAULT_ENV_NAME)

        self.scenarios.append(s)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"{'#' * 60}")
    print(f"# E2E Test Suite")
    print(f"# Repository: {REPO_FULL}")
    print(f"# Zone:       {ZONE}")
    print(f"# Scenario:   {SCENARIO}")
    print(f"# Network:    {make_network_name(DEFAULT_ENV_NAME)} (default)")
    print(f"#             {make_network_name('e2etest')} (scale-up)")
    print(f"{'#' * 60}")

    runner = E2ETestRunner()
    all_passed = runner.run()

    total_pass = sum(
        sum(1 for a in s.assertions if a["passed"])
        for s in runner.scenarios
    )
    total_fail = sum(
        sum(1 for a in s.assertions if not a["passed"])
        for s in runner.scenarios
    )

    print(f"\n{'#' * 60}")
    print(f"# FINAL: {total_pass} passed, {total_fail} failed")
    print(f"{'#' * 60}")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
