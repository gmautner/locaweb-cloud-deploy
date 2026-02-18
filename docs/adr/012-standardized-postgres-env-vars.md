# ADR-012: Standardized PostgreSQL Environment Variables

## Status

Accepted (supersedes ADR-009)

## Context

The platform previously used aliased secrets (ADR-009) to map infrastructure-level secret names (`POSTGRES_USER`, `POSTGRES_PASSWORD`) to application-level names (`DB_USERNAME`, `DB_PASSWORD`). Other database variables used a mix of naming conventions: `DB_HOST`, `DB_PORT`, `DB_NAME`.

This created unnecessary indirection. The aliasing added complexity, and the inconsistent naming (some `DB_*`, some `POSTGRES_*`) made the contract between the platform and the application unclear.

## Decision

Standardize on `POSTGRES_*` naming for all database environment variables passed to the application container. The platform provides:

| Variable | Type | Source |
|----------|------|--------|
| `POSTGRES_HOST` | Clear | DB VM internal IP |
| `POSTGRES_DB` | Clear | Hardcoded to `postgres` (ADR-025) |
| `POSTGRES_USER` | Clear | Hardcoded to `postgres` (ADR-025) |
| `POSTGRES_PASSWORD` | Secret | GitHub repository secret |
| `DATABASE_URL` | Secret | Composed in `.kamal/secrets` as `postgres://postgres:$POSTGRES_PASSWORD@$POSTGRES_HOST:5432/postgres` |

**Update (ADR-025):** With the switch to `supabase/postgres`, `POSTGRES_USER` and `POSTGRES_DB` are now hardcoded clear env vars (`postgres`/`postgres`) rather than configurable secrets/values. Only `POSTGRES_PASSWORD` remains as a secret. `DATABASE_URL` is composed via shell variable interpolation in the `.kamal/secrets` file, making it available as a secret since it contains the password.

## Consequences

**Positive:**

- Consistent naming convention -- all database variables use the `POSTGRES_*` prefix.
- No aliasing indirection -- secrets are passed with their original names.
- Clear contract between platform and application, documented in the architecture doc.
- The `POSTGRES_*` naming matches the PostgreSQL Docker image conventions, reducing cognitive overhead.

**Negative:**

- Applications that previously relied on custom variables names like, for example, `DB_HOST`, `DB_NAME`, `DB_USERNAME`, or `DB_PASSWORD` must be updated to use the standardized `POSTGRES_*` variable names.
