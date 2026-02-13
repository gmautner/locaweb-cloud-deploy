# ADR-018: fail2ban for SSH Brute-Force Protection

**Status:** Accepted
**Date:** 2026-02-13

## Context

All VMs have SSH (port 22) exposed to the internet via `0.0.0.0/0` firewall rules, as Kamal requires root SSH access for deployment. Smoke testing showed that scanners begin brute-force password guessing within seconds of a VM going live -- two IPs were banned within the first five minutes of a test VM's existence.

The Ubuntu 24.04 template also sets `PasswordAuthentication yes` via cloud-init (`/etc/ssh/sshd_config.d/50-cloud-init.conf`), making password-based attacks viable even though the deployment uses key-based authentication.

## Decision

Install and configure fail2ban on all VMs (web, worker, DB) via cloud-init userdata scripts. The configuration uses stricter-than-default settings:

| Setting  | Default | Chosen  |
|----------|---------|---------|
| maxretry | 5       | 3       |
| bantime  | 600s    | 3600s   |
| findtime | 600s    | 600s    |
| mode     | normal  | aggressive |

Aggressive mode adds detection of connection flooding and protocol-level abuse beyond just failed password attempts.

A new `scripts/userdata/worker_vm.sh` script was created for workers, which previously had no cloud-init userdata.

## Consequences

- All VMs are protected against SSH brute-force attacks from first boot.
- Banned IPs are blocked for 1 hour via nftables rules (the default backend on Ubuntu 24.04).
- The `apt-get install fail2ban` step adds a few seconds to VM first-boot time.
- This does not replace the planned IP filtering for SSH (restricting to GitHub Actions runner ranges), which remains a separate TODO.
