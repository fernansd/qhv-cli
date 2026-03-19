# LEARNED_LESSONS.md

## Purpose

This file captures durable lessons from debugging and validating `qhv` so future changes do not repeat the same failures.

## Product And Runtime Lessons

- The README user flow is the authoritative UX contract: `check`, `create`, `status`, `ssh`, `delete` from the repo root with default `.qhv/` state.
- A passing constrained integration test is not sufficient on its own. The default README flow can still fail if startup, port, or key-discovery assumptions differ.
- `qhv create` should only report success after the guest exposes an SSH banner. "QEMU process survived briefly" is not a useful success condition for users.
- The stable default VM baseline on this host is `1` vCPU and `1024` MiB. Heavier defaults caused the manual README flow to fail even when the constrained test passed.
- Host port `2222` is a real environmental dependency for the default README flow. If it is occupied, QEMU startup fails even when guest boot and storage are otherwise correct.

## Bootstrap And Networking Lessons

- Cloud-init `user-data` structure matters. Misplaced `ssh_authorized_keys` or `lock_passwd` entries can silently break guest access.
- The NoCloud seed must be discoverable as a local vfat device labeled `cidata`. For this repo, that means a read-only QEMU `vvfat` device with `file.label=cidata`.
- Ubuntu cloud images on this project expect predictable `en*` NIC names. Netplan stanzas that target a made-up interface name will leave the guest without networking.
- Package installation failures during cloud-init do not necessarily mean guest bootstrap failed. Network and SSH readiness need to be checked independently.

## Testing Lessons

- User-flow commands must be run sequentially during manual validation. Running `create` and `status`, or `delete` and `status`, in parallel produces misleading results.
- There should be two complementary real-VM paths:
  - a constrained integration test with explicit state dir and explicit SSH key
  - a README-style integration test that uses default `.qhv/` state and default key discovery semantics
- The README-style integration test should run in an isolated temporary working directory so it still exercises default `.qhv/` behavior without touching the repo's real state.
- The README-style integration test should seed a temporary `HOME` or `USERPROFILE` with `id_ed25519` so it uses the same default key-discovery path as real users.
- README-flow integration coverage should skip cleanly when the host's default SSH forward port is already occupied, because that is an environmental conflict rather than a code regression.

## Operational Lessons

- Windows `taskkill` can emit noisy or localized output even when cleanup succeeds. Suppress or normalize it unless the failure is actionable.
- Always inspect both `qemu.stderr.log` and `serial.log` when startup or SSH readiness fails. One without the other often misses the real cause.
- Keep repo guidance synchronized with code. This repo previously carried stale notes about Ubuntu image format and seed-drive attachment, which made debugging slower and less reliable.
