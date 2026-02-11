# ADR-014: E2E Test Orchestration via Real Workflow Triggers

## Status

Accepted

## Context

The project had an infrastructure test suite (`test-infrastructure.yml` / `test_infrastructure.py`) that validates CloudStack resource provisioning, but it never ran Kamal, never deployed the application, and never verified that the deployed application actually works. There was no validation that the full pipeline -- from `workflow_dispatch` through provisioning, Docker build, Kamal deploy, and container startup -- produces a working application.

Additionally, the sample Flask app crashed without a database connection, preventing web-only deployments from passing health checks.

Three approaches were considered:

1. **Extend test_infrastructure.py** to also run Kamal and verify the app. This would require duplicating the Kamal installation, config generation, and deployment logic from deploy.yml in the test script.
2. **Mock the deploy workflow** with a simplified test version. This would test a different code path than production, reducing confidence.
3. **Trigger the real deploy.yml** from an orchestrator workflow, wait for completion, then verify application behavior. This tests the actual production pipeline.

## Decision

Implement E2E testing by triggering the real `deploy.yml` and `teardown.yml` workflows via the `gh` CLI from a separate `e2e-test.yml` orchestrator workflow.

The orchestrator (`scripts/e2e_test.py`):
- Records the latest run ID before triggering a workflow.
- Triggers `gh workflow run deploy.yml` with specific inputs.
- Polls until a new run appears (ID > previous), then watches it with `gh run watch --exit-status`.
- Downloads the `provision-output` artifact to get VM IPs.
- Verifies the deployed application via HTTP (health checks, page content, form submissions, file uploads) and SSH (mount points, disk sizes, Docker container env vars).
- Triggers `teardown.yml` at the end of each scenario.

To prevent deadlocks, the E2E workflow uses its own concurrency group (`e2e-test-${{ github.repository }}`), separate from the `deploy-${{ github.repository }}` group shared by `deploy.yml` and `teardown.yml`. Without this separation, a triggered deploy would queue behind the E2E run that is waiting for it.

The sample app was also updated to degrade gracefully without a database (`DB_CONFIGURED` flag based on `POSTGRES_HOST`), enabling web-only deployments to pass health checks.

## Consequences

**Positive:**

- Tests the real production deployment pipeline, not mocks or abstractions.
- Validates application behavior end-to-end: HTTP endpoints, database operations, file uploads, environment variable injection, disk mounts, and disk sizes.
- Catches integration issues that infrastructure-only tests miss (Kamal config generation bugs, container startup failures, health check logic errors).
- Follows the same `TestScenario` assertion pattern as `test_infrastructure.py` for consistent results output and GitHub step summary rendering.
- The graceful DB handling also benefits production use cases where users want web-only deployments.

**Negative:**

- E2E tests are slow: 8-15 minutes per scenario due to CloudStack provisioning and Kamal deployment.
- E2E tests share the production resource namespace (`github.repository_id`), so running them tears down any existing production deployment.
- The workflow triggering pattern (poll for new run ID) has a race window if multiple runs are triggered simultaneously, though the concurrency group prevents this in practice.
- Requires additional GitHub secrets/variables (`KAMAL_MY_VAR`, `KAMAL_MY_SECRET`) to be configured for environment variable verification tests.
