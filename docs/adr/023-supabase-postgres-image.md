# ADR-023: Switch to supabase/postgres with Automated Tag Resolution

**Status:** Rejected (see ADR-025 for revised approach)
**Date:** 2026-02-17

## Context

The project used the official `postgres:16` Docker image as the database accessory. While functional, the official image ships with only the core PostgreSQL extensions. As application requirements grow, pre-installed extensions (e.g. pgvector, pg_cron, pgjwt) become valuable.

`supabase/postgres` is a PostgreSQL image maintained by Supabase that bundles a curated set of popular extensions on top of the official PostgreSQL distribution. It is well-maintained and widely used.

However, unlike the official postgres image which supports short tags like `16` or `17`, supabase/postgres only publishes 4-digit tags like `17.6.1.084`. Some tags also carry suffixes (e.g. `-orioledb`) for alternative storage engines, requiring a tag resolution mechanism at deploy time.

## Decision

Rejected. The `supabase/postgres` image introduces too many customizations that deviate from standard PostgreSQL behavior, requiring extensive workarounds to function in our deployment model. We prefer solutions that work out of the box and behave without surprises.

Specific issues encountered:

- **Hardcoded `supabase_admin` role**: The entrypoint init script (`migrate.sh`) expects a `supabase_admin` PostgreSQL role, removing the caller's freedom to choose their own database username via the `POSTGRES_USER` secret.
- **`listen_addresses = localhost`**: The image's `initdb`-generated `postgresql.conf` defaults `listen_addresses` to `localhost`, silently preventing external connections. The supabase config at `/etc/postgresql/postgresql.conf` sets `*`, but PostgreSQL loads the PGDATA copy instead, causing a hard-to-diagnose failure where the container appears healthy but refuses all network connections.
- **Non-standard tag scheme**: The 4-component version tags (`17.x.y.z`) required a custom tag resolution script and Docker Hub API queries at deploy time, adding complexity and a runtime dependency.

The cumulative effect — a required username, a hidden networking default, and a custom tag resolver — is too much tweaking for what should be a drop-in database image. The official `postgres:17` image works correctly with no additional configuration.

The primary motivation for evaluating `supabase/postgres` was its bundled extension set. Providing a mechanism for callers to install PostgreSQL extensions on the official image remains an open task.

## Original Proposal

1. Switch the database accessory image from `postgres:16` to `supabase/postgres` with a tag under major version 17.
2. Automate tag resolution with a script that queries Docker Hub at deploy time.
3. Pass the resolved image via `POSTGRES_IMAGE` to the config generation script.
