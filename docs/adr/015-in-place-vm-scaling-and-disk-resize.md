# ADR-015: In-Place VM Scaling and Disk Resize

## Status

Accepted

## Context

The idempotent provisioning script (ADR-005) treats existing VMs and disks as "skip" — if a resource exists by name, it reuses it without checking whether the configuration has changed. This means users who change their VM plan (e.g., `small` → `medium`) or increase disk size must teardown and redeploy, causing downtime and data loss on disks.

CloudStack provides APIs for in-place changes:

- `scaleVirtualMachine` — changes a VM's service offering (CPU/RAM) without destroying it. Some offerings support live scaling; others require the VM to be stopped first.
- `resizeVolume` — grows a data disk online. CloudStack does not support shrinking volumes.

## Decision

Enhance the provisioning logic to detect and apply configuration changes to existing resources:

1. **VM Scaling:** When an existing VM's `serviceofferingid` differs from the desired offering, stop the VM, call `scaleVirtualMachine`, then start it again. Hot (live) scaling is not attempted because CloudStack rejects it for fixed service offerings, which is always our case.
2. **Disk Resize (grow only):** When an existing volume's `size` is smaller than the desired size, call `resizeVolume`. If the desired size is smaller than the current size, fail with an error — shrinking is not supported by CloudStack and would cause data loss.
3. **No-op on match:** When the offering and disk size already match, the existing skip behavior is preserved.

The `find_vm` helper now returns a full VM dict (including `serviceofferingid`) instead of just the ID string. The `find_volume` helper now includes `size` in its filter. Both changes are internal to the provisioning script and do not affect the output JSON format.

## Consequences

**Positive:**

- Users can change VM plans without teardown/redeploy, reducing downtime.
- Disk growth is non-destructive — existing data is preserved.
- Explicit rejection of disk shrink prevents accidental data loss.
- Going directly to offline scaling avoids wasted time on failed hot-scale attempts and retries.

**Negative:**

- Offline scaling (stop → scale → start) introduces brief downtime for every VM scaling operation. This is unavoidable given that CloudStack rejects live scaling for fixed service offerings, which is always our case.
- The script now makes additional API calls to compare current vs. desired state, slightly increasing provisioning time even when no changes are needed.
- Disk resize is grow-only. Users who need to shrink a disk must still teardown and redeploy (or manually manage the volume outside the tool).
