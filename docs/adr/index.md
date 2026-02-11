# Architectural Decision Records

This directory contains the Architectural Decision Records (ADRs) for the `locaweb-ai-deploy` project.

| ADR | Title | Status |
|-----|-------|--------|
| [ADR-001](001-kamal-for-deployment.md) | Use Kamal 2 for Container Deployment (not Kubernetes) | Accepted |
| [ADR-002](002-ghcr-with-github-token.md) | Use ghcr.io with GITHUB_TOKEN for Container Registry | Accepted |
| [ADR-003](003-dynamic-kamal-config-generation.md) | Generate Kamal Config Dynamically at Deploy Time | Accepted |
| [ADR-004](004-cloudmonkey-cli-for-cloudstack.md) | CloudMonkey CLI for CloudStack API Interaction | Accepted |
| [ADR-005](005-idempotent-provisioning.md) | Idempotent Provisioning with Name-Based Lookup | Accepted |
| [ADR-006](006-static-nat-for-public-ip.md) | Static NAT (1:1) for Public IP Assignment | Accepted |
| [ADR-007](007-pgdata-subdirectory.md) | PGDATA Subdirectory for ext4 Volume Compatibility | Accepted |
| [ADR-008](008-nip-io-wildcard-dns.md) | nip.io for Wildcard DNS | Accepted |
| [ADR-009](009-aliased-secrets.md) | Aliased Secrets for Environment Variable Mapping | Superseded by ADR-012 |
| [ADR-010](010-fail-fast-secret-validation.md) | Fail-Fast Secret Validation | Accepted |
| [ADR-011](011-teardown-and-redeploy-recovery.md) | Teardown-and-Redeploy as Recovery Strategy | Accepted |
| [ADR-012](012-standardized-postgres-env-vars.md) | Standardized PostgreSQL Environment Variables | Accepted |
| [ADR-013](013-kamal-prefix-env-vars.md) | KAMAL_ Prefix Convention for Custom Environment Variables | Accepted |
| [ADR-014](014-e2e-test-orchestration.md) | E2E Test Orchestration via Real Workflow Triggers | Accepted |
| [ADR-015](015-in-place-vm-scaling-and-disk-resize.md) | In-Place VM Scaling and Disk Resize | Accepted |
