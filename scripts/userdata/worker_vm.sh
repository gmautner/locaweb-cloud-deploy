#!/bin/bash
# Userdata script for Worker VM
# Installs fail2ban to block SSH brute-force attempts.
# Docker is installed automatically by Kamal on first deploy.
set -euo pipefail

# --- fail2ban: block SSH brute-force attempts ---
apt-get update -qq
apt-get install -y -qq fail2ban
cat > /etc/fail2ban/jail.local << 'F2BEOF'
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 3

[sshd]
enabled = true
mode = aggressive
F2BEOF
systemctl restart fail2ban
