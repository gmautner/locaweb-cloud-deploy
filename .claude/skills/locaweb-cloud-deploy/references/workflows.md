# Caller Workflow Reference

## Table of Contents

- [Preview Workflow (Recommended First Step)](#preview-workflow-recommended-first-step)
- [Production Workflow](#production-workflow)
- [Deploy Input Reference](#deploy-input-reference)
- [Complete Example (All Inputs)](#complete-example-all-inputs)
- [Workflow Permissions](#workflow-permissions)
- [Passing Outputs to Downstream Jobs](#passing-outputs-to-downstream-jobs)

## Preview Workflow (Recommended First Step)

Start with this. No domain, triggered on push, uses nip.io for immediate access.

```yaml
# .github/workflows/deploy-preview.yml
name: Deploy Preview
on:
  push:
    branches: [main]

permissions:
  contents: read
  packages: write

jobs:
  deploy:
    uses: gmautner/locaweb-ai-deploy/.github/workflows/deploy.yml@main
    with:
      env_name: "preview"
      zone: "ZP01"
      db_enabled: true
      db_plan: "medium"
    secrets:
      CLOUDSTACK_API_KEY: ${{ secrets.CLOUDSTACK_API_KEY }}
      CLOUDSTACK_SECRET_KEY: ${{ secrets.CLOUDSTACK_SECRET_KEY }}
      SSH_PRIVATE_KEY: ${{ secrets.SSH_PRIVATE_KEY }}
      POSTGRES_USER: ${{ secrets.POSTGRES_USER }}
      POSTGRES_PASSWORD: ${{ secrets.POSTGRES_PASSWORD }}
```

After this runs successfully, the app is accessible at `http://<web_ip>.nip.io`. The `web_ip` is visible in the workflow run summary.

## Production Workflow

Add this when the application is ready for production. Triggered on version tags (`v*`), which ensures the production deployment always uses the exact code from the tagged commit. Uses a custom domain with automatic HTTPS. Note how the `secrets:` block maps the `_PROD` suffixed secrets (`SSH_PRIVATE_KEY_PROD`, `POSTGRES_USER_PROD`, `POSTGRES_PASSWORD_PROD`) to the workflow's standard secret names, keeping production credentials fully separate from preview.

```yaml
# .github/workflows/deploy-production.yml
name: Deploy Production
on:
  push:
    tags: ["v*"]  # Triggered by version tags (e.g., git tag v1.0.0 && git push --tags)

permissions:
  contents: read
  packages: write

jobs:
  deploy:
    uses: gmautner/locaweb-ai-deploy/.github/workflows/deploy.yml@main
    with:
      env_name: "production"
      zone: "ZP01"
      domain: "myapp.example.com"
      web_plan: "medium"
      db_enabled: true
      db_plan: "medium"
      db_disk_size_gb: 50
      blob_disk_size_gb: 50
    secrets:
      CLOUDSTACK_API_KEY: ${{ secrets.CLOUDSTACK_API_KEY }}
      CLOUDSTACK_SECRET_KEY: ${{ secrets.CLOUDSTACK_SECRET_KEY }}
      SSH_PRIVATE_KEY: ${{ secrets.SSH_PRIVATE_KEY_PROD }}
      POSTGRES_USER: ${{ secrets.POSTGRES_USER_PROD }}
      POSTGRES_PASSWORD: ${{ secrets.POSTGRES_PASSWORD_PROD }}
```

To deploy to production: `git tag v1.0.0 && git push --tags`. The workflow checks out the tagged commit, so the Dockerfile and source code match the tag exactly.

## Deploy Input Reference

All inputs, their types, defaults, and when to use them:

| Input | Type | Default | When to set |
|-------|------|---------|-------------|
| `env_name` | string | `"preview"` | Always set explicitly to name the environment. Each env_name creates fully isolated infrastructure. |
| `zone` | string | `"ZP01"` | Usually leave as `ZP01`. Use `ZP02` for geographic redundancy. |
| `domain` | string | `""` (empty) | Set for production environments where HTTPS is needed. Leave empty for preview/dev (uses nip.io). See SKILL.md for the DNS setup procedure. |
| `web_plan` | string | `"small"` | Choose based on runtime footprint and environment. See [scaling.md](scaling.md) for plan specs. |
| `blob_disk_size_gb` | number | `20` | Increase if the app stores files (uploads, media). Consider environment: preview can use smaller, production may need more. Can only grow, never shrink. |
| `workers_enabled` | boolean | `false` | Set `true` when the app needs background processing. |
| `workers_replicas` | number | `1` | Number of worker VMs. Only relevant when `workers_enabled: true`. |
| `workers_cmd` | string | `"sleep infinity"` | Command to run in worker containers. e.g., `"celery -A tasks worker --loglevel=info"` or `"python worker.py"`. |
| `workers_plan` | string | `"small"` | VM size for workers. Choose based on worker workload intensity. See [scaling.md](scaling.md). |
| `db_enabled` | boolean | `false` | Set `true` when the app needs PostgreSQL. Requires `POSTGRES_USER` and `POSTGRES_PASSWORD` secrets. |
| `db_plan` | string | `"medium"` | VM size for the database. Choose based on expected data size and query complexity. See [scaling.md](scaling.md). |
| `db_disk_size_gb` | number | `20` | PostgreSQL data disk size. Consider environment and expected data growth. Can only grow, never shrink. |
| `automatic_reboot` | boolean | `true` | Enable automatic reboot after unattended security upgrades. Usually leave as default. |
| `automatic_reboot_time_utc` | string | `"05:00"` | When automatic reboots happen. Usually leave as default. |
| `recover` | boolean | `false` | Reserved for future disaster recovery workflows. Do not use. |
| `env_vars` | string | `""` | Dotenv-formatted clear environment variables. See [env-vars.md](env-vars.md). |

### Inputs to leave at defaults

For most deployments, omit these (let defaults apply):
- `automatic_reboot` / `automatic_reboot_time_utc` -- security auto-updates are good defaults
- `recover` -- reserved for future use
- `blob_disk_size_gb` -- 20 GB is sufficient for most apps unless heavy file storage

## Complete Example (All Inputs)

Full-stack example with web, database, and workers. Every input is shown with required/optional and default value annotations.

```yaml
# .github/workflows/deploy-preview.yml
name: Deploy Preview
on:
  push:
    branches: [main]

permissions:
  contents: read
  packages: write

jobs:
  deploy:
    uses: gmautner/locaweb-ai-deploy/.github/workflows/deploy.yml@main
    with:
      env_name: "preview"                    # Required
      zone: "ZP01"                           # Required (options: ZP01, ZP02)
      domain: ""                             # Optional, default: "" (empty = nip.io, no HTTPS)
      web_plan: "small"                      # Optional, default: "small"
      blob_disk_size_gb: 20                  # Optional, default: 20 (grow only, never shrink)
      workers_enabled: true                  # Optional, default: false
      workers_replicas: 2                    # Optional, default: 1 (only when workers_enabled: true)
      workers_cmd: "python worker.py"        # Optional, default: "sleep infinity"
      workers_plan: "small"                  # Optional, default: "small"
      db_enabled: true                       # Optional, default: false
      db_plan: "medium"                      # Optional, default: "medium"
      db_disk_size_gb: 20                    # Optional, default: 20 (grow only, never shrink)
      automatic_reboot: true                 # Optional, default: true
      automatic_reboot_time_utc: "05:00"     # Optional, default: "05:00"
      recover: false                         # Optional, default: false (reserved for future DR)
      env_vars: |-                           # Optional, default: "" (dotenv format)
        APP_ENV=preview
        LOG_LEVEL=debug
    secrets:
      CLOUDSTACK_API_KEY: ${{ secrets.CLOUDSTACK_API_KEY }}       # Required
      CLOUDSTACK_SECRET_KEY: ${{ secrets.CLOUDSTACK_SECRET_KEY }} # Required
      SSH_PRIVATE_KEY: ${{ secrets.SSH_PRIVATE_KEY }}             # Required
      POSTGRES_USER: ${{ secrets.POSTGRES_USER }}                 # Required when db_enabled: true
      POSTGRES_PASSWORD: ${{ secrets.POSTGRES_PASSWORD }}         # Required when db_enabled: true
      SECRET_ENV_VARS: |-                                        # Optional (dotenv format)
        API_KEY=${{ secrets.API_KEY }}
        SMTP_PASSWORD=${{ secrets.SMTP_PASSWORD }}
```

## Workflow Permissions

The deploy caller workflow **must** include:

```yaml
permissions:
  contents: read
  packages: write
```

`packages: write` is required for pushing container images to ghcr.io. The teardown workflow does not need `packages: write`.

## Passing Outputs to Downstream Jobs

The deploy workflow exposes outputs that can be consumed by subsequent jobs:

```yaml
jobs:
  deploy:
    uses: gmautner/locaweb-ai-deploy/.github/workflows/deploy.yml@main
    with:
      # ... inputs
    secrets:
      # ... secrets

  notify:
    needs: deploy
    runs-on: ubuntu-latest
    steps:
      - run: |
          echo "Web IP: ${{ needs.deploy.outputs.web_ip }}"
          echo "Worker IPs: ${{ needs.deploy.outputs.worker_ips }}"
          echo "DB IP: ${{ needs.deploy.outputs.db_ip }}"
          echo "DB Internal IP: ${{ needs.deploy.outputs.db_internal_ip }}"
```

Available outputs:
- `web_ip` -- Public IP of the web VM
- `worker_ips` -- JSON array of worker VM public IPs (e.g., `["1.2.3.4","5.6.7.8"]`)
- `db_ip` -- Public IP of the database VM (SSH access only)
- `db_internal_ip` -- Private IP of the database VM (used by the app internally)
