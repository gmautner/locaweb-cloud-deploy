# ADR-007: PGDATA Subdirectory for ext4 Volume Compatibility

## Status

Accepted

## Context

CloudStack data disks are formatted as ext4 when attached to VMs. The ext4 filesystem creates a `lost+found` directory at the root of every formatted volume. This is a standard ext4 behavior and cannot be suppressed.

When a data disk is mounted directly as the PostgreSQL data directory (e.g., `/var/lib/postgresql/data`), the `initdb` process fails because it expects the target directory to be completely empty. The presence of `lost+found` causes the following error:

```
initdb: error: directory "/var/lib/postgresql/data" exists but is not empty
```

This is a well-known issue when running PostgreSQL in Docker with ext4-backed volumes.

## Decision

Use a host-level subdirectory to isolate `lost+found` from the PostgreSQL data directory.

**Current approach (ADR-025):** The DB VM cloud-init script creates `/data/db/pgdata`, and the Docker volume mapping binds `/data/db/pgdata:/var/lib/postgresql/data`. The container sees a clean mount point without `lost+found`. No `PGDATA` env var is needed.

**Previous approach:** Set the `PGDATA` environment variable to `/var/lib/postgresql/data/pgdata` while mounting the host volume at `/var/lib/postgresql/data`. This achieved the same isolation but required the env var.

On the host, the actual data path is `/data/db/pgdata`.

## Consequences

**Positive:**

- PostgreSQL initializes cleanly on ext4-formatted CloudStack data disks without any filesystem workarounds.
- This is the standard pattern recommended by the official PostgreSQL Docker image documentation.
- No special formatting or mount options are required for the data disk.

**Negative:**

- The data directory is one level deeper than the mount point, which can be mildly confusing when inspecting the filesystem directly.
- Backup or maintenance scripts that operate on the volume mount point must account for the `pgdata` subdirectory.
