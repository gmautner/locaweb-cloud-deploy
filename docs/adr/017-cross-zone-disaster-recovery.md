# ADR-017: Cross-Zone Disaster Recovery via Snapshot Replicas

**Status:** Accepted
**Date:** 2026-02-12

## Context

CloudStack snapshot policies already replicate snapshots to all available zones. When a deployment in one zone is lost (hardware failure, zone outage) or when we want to migrate to a different zone, the data is available as snapshot replicas -- but the provisioning script had no mechanism to create volumes from those snapshots.

We needed a way to recover a deployment into a different zone using the replicated snapshot data, without requiring manual CloudStack API interaction.

## Decision

Add a `recover` boolean input to the deploy workflow. When enabled, the provisioning script:

1. Runs pre-flight checks: verifies no existing deployment (network or volumes) in the target zone, and that required snapshots exist in BackedUp state.
2. Creates data volumes from the latest available snapshots (both MANUAL and RECURRING types are considered) instead of blank disks.
3. Tags and attaches the recovered volumes to the new VMs.
4. Creates new snapshot policies on the recovered volumes for ongoing protection.

The recovery flow reuses the existing provisioning pipeline for all non-disk resources (network, VMs, IPs, firewall rules). Only the disk creation step differs.

No changes to userdata scripts were needed because both `web_vm.sh` and `db_vm.sh` already check `blkid` before formatting, so recovered volumes (which already have ext4 filesystems with data) are not wiped.

The `find_network` and `find_volume` helpers were made zone-aware (accepting an optional `zone_id` parameter) as a general improvement that benefits both normal and recovery flows.

## Consequences

### Positive

- Cross-zone disaster recovery is a single workflow dispatch with `recover=true` and a different `zone`.
- Pre-flight checks prevent accidental data loss by refusing to recover over an existing deployment.
- Recovered deployments get their own snapshot policies, maintaining the same data protection as fresh deployments.
- No changes to the application or userdata scripts were required.

### Negative

- Recovery depends on snapshot replication having completed to the target zone. If snapshots haven't replicated yet, recovery will fail with a clear error message.
- The `recover` input must be used with a zone different from the original deployment. Using it in the same zone where the deployment still exists will fail the pre-flight checks.
- Manual snapshots must be created for immediate recovery testing since daily snapshots run on a schedule (06:00 UTC).

### Neutral

- Zone-aware `find_network` and `find_volume` are backward-compatible (zone_id defaults to None, preserving existing behavior).
