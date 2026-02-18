# ADR-025: Switch to supabase/postgres with Hardcoded Version

**Status:** Accepted (supersedes ADR-023)
**Date:** 2026-02-18

## Context

The project used the official `postgres:17` Docker image (ADR-023 rejected `supabase/postgres` previously due to complexity around tag resolution, the `supabase_admin` role, and `listen_addresses` defaults). However, the official image ships only core PostgreSQL extensions. As application requirements grow, pre-installed extensions (pgvector, pg_cron, pgmq, pg_jsonschema, and 60+ others) become valuable.

A re-evaluation of `supabase/postgres` found a simpler approach that avoids the original pain points:

1. **Pin a specific version tag** instead of building a tag discovery mechanism.
2. **Use `-D /etc/postgresql`** in the CMD so the Supabase-provided config (which sets `listen_addresses = '*'` and configures `shared_preload_libraries`) is loaded instead of the PGDATA copy that defaults to `localhost`.
3. **Drop user/database customization** -- always use `postgres`/`postgres`, matching Supabase's recommended usage and avoiding conflicts with the `supabase_admin` role setup.

## Decision

Switch the database accessory image from `postgres:17` to `supabase/postgres` with a pinned version tag:

- **Image**: `supabase/postgres` with a pinned 4-component version tag (e.g. `17.x.y.z`).
- **CMD**: `postgres -D /etc/postgresql -c shared_buffers=1GB -c effective_cache_size=3GB ...` -- the `-D /etc/postgresql` flag loads Supabase's config which sets `listen_addresses = '*'` and appropriate `shared_preload_libraries`.
- **User/database**: Hardcoded to `postgres`/`postgres` (clear env vars in the app container, not secrets). The `POSTGRES_USER` secret is removed from the workflow contract.
- **Accessory env**: Only `POSTGRES_PASSWORD` as a secret. No `POSTGRES_DB`, `PGDATA`, or `POSTGRES_USER` env vars needed.
- **Volume mapping**: `/data/db/pgdata:/var/lib/postgresql/data` -- the `pgdata` subdirectory is created by the DB VM cloud-init script, moving the ext4 `lost+found` workaround (ADR-007) from a `PGDATA` env var to a host-level subdirectory. The container sees a clean mount point.

## Consequences

**Positive:**

- 60+ bundled extensions available out of the box (pgvector, pg_cron, pgmq, pg_jsonschema, pgjwt, pg_stat_statements, etc.) with no additional installation step.
- Simpler secret management -- only `POSTGRES_PASSWORD` is needed as a secret. `POSTGRES_USER` and `POSTGRES_DB` are hardcoded clear env vars.
- The pinned tag avoids the complexity of a tag resolution mechanism that ADR-023 originally proposed.
- The `-D /etc/postgresql` approach cleanly resolves the `listen_addresses = localhost` issue identified in ADR-023.

**Negative:**

- The image tag must be manually updated when upgrading PostgreSQL versions (no `postgres:17`-style floating tag).
- Applications that previously relied on choosing a custom database user or name no longer have that flexibility.
- The `supabase/postgres` image is larger than the official `postgres:17` image due to bundled extensions.
