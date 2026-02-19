# ADR-026: Input-Hash Caching for Faster Consecutive Deploys

**Status:** Accepted
**Date:** 2026-02-19

## Context

The deploy workflow takes 4-6 minutes on every run, even when infrastructure inputs haven't changed. Several steps are redundant on consecutive deploys with identical inputs:

- **Provision infrastructure** (~60-120s) makes many CloudStack API calls even though the provisioning script is idempotent and would find all resources already exist.
- **Configure unattended upgrades** (~30-60s) SSHes into every VM to write the same config files.
- **Install CloudMonkey** (~5-10s) downloads and configures the CLI binary.
- **Build configuration** (~2s) assembles the same JSON config.
- **Install Kamal** (~20-30s) runs `gem install` from RubyGems on every run.

Additionally, `kamal setup` (which installs Docker and bootstraps accessories) runs on every deploy, even when the servers already have Docker installed and accessories running from a previous deploy.

For vibe coders iterating on their apps, the deploy feedback loop needs to be as fast as possible.

## Decision

### Infrastructure cache

Hash all workflow inputs via `sha256sum` on the JSON-serialized `inputs` object. Use `actions/cache@v4` to cache `/tmp/provision-output.json` keyed by this hash with format `infra-{repository}-{env_name}-{hash}`.

On cache hit, skip: Build configuration, Install CloudMonkey, Provision infrastructure, and Configure unattended upgrades.

Hashing `toJSON(inputs)` as a whole (rather than listing individual fields) means any new input added in the future automatically participates in cache invalidation with zero maintenance.

### Kamal gem cache

Cache the gem installation directory (`~/.gems`) with key `kamal-{runner.os}-v1`. On cache hit, skip `gem install`. A dedicated "Configure gem path" step sets `GEM_HOME` and `PATH` via `GITHUB_ENV` and `GITHUB_PATH` before the cache step, so both install and runtime find the correct paths.

Gems are installed to `~/.gems` (user-writable) instead of `/var/lib/gems` (root-owned) because `actions/cache` cannot restore files to root-owned directories.

### kamal setup vs kamal deploy

Use the infrastructure cache hit as a signal for server readiness:

- **Cache miss** (first deploy or inputs changed): Run `kamal setup`, which installs Docker, bootstraps accessories, and deploys the application.
- **Cache hit** (consecutive deploy, same inputs): Run `kamal deploy`, which skips Docker installation and accessory bootstrapping, only building and deploying the new application image.

### Safety mechanisms

- **`recover: true` bypasses the cache entirely** (`if: inputs.recover != true` on the cache step), ensuring disaster recovery always runs full provisioning.
- **Any input change invalidates the cache**, triggering a full provisioning run. This includes Kamal-only inputs like `domain` or `workers_cmd` — an acceptable trade-off for zero-maintenance cache invalidation.
- **The provisioning script is idempotent**, so a stale cache only means an unnecessary full run on the next deploy; it cannot cause incorrect infrastructure state.
- **Steps that read from `provision-output.json`** (Set outputs, Upload artifact, Print summary) always run regardless of cache, since the file is either freshly created or restored from cache.

## Consequences

### Positive

- Consecutive deploys with unchanged inputs save ~110-220s (skipping provisioning, CloudMonkey, unattended upgrades, and gem install).
- `kamal deploy` is faster than `kamal setup` because it skips Docker installation and accessory bootstrapping.
- No manual list of cache-participating fields to maintain — adding a new workflow input automatically participates.
- The Kamal gem cache is persistent across all deploys (OS-keyed), saving ~20-30s even on cache-miss infrastructure runs.

### Negative

- Kamal-only input changes (e.g., `domain`, `workers_cmd`) trigger unnecessary re-provisioning. This is acceptable because these changes are infrequent and the provisioning script is idempotent.
- The cache key does not include the infrastructure scripts themselves (only inputs). If provisioning logic changes in a new commit, the cache may still hit. This is safe because idempotent provisioning handles already-existing resources, and the next input change will invalidate the cache.
