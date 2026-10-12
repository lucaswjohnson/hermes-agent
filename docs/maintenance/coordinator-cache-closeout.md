# Coordinator cache closeout

## Change

The existing scratch cleanup now expires old generated Metro cache files on their own. Previously, any recent write under `cache/scratch/metro-cache` kept every old file across all 256 shards.

Cleanup only considers regular cache files with hexadecimal names inside two-character hexadecimal shard directories. Recent files, symlinks, unknown files, and unknown directories remain. The shared cache root is never passed to recursive scratch deletion.

A same-user process holds the whole cache if it references the cache through its command, working directory, executable, open files, or mapped files. Active Metro/Expo commands also hold it. Enabled Hermes jobs that refer to the cache or Metro/Expo hold it. If inspection of same-user processes or scheduled jobs is incomplete, cleanup stops. Cache cleanup does not kill consumers.

The existing retention interval and `prune_scratch_dir` entry point remain in use. This adds no scheduler or closeout framework. Ordinary non-Metro scratch pruning is unchanged, including its existing process-reaping behavior. This patch does not establish that unrelated age-based worktree deletion is safe. Replace this narrow extension when upstream scratch retention provides equivalent generated-cache expiry and consumer holds.

## Exercised on October 12, 2026

- Existing pruning and unreadable-subtree checks: six passed. A broader run passed 18 checks and failed two unchanged container-permission checks. The initially cached pytest interpreter lacked YAML. Using the runtime interpreter with the existing cached pytest resolved the environment error without installation.
- Direct testing preserved a live process and its cache, then removed one stale generated file after that process exited. Recent data, an unknown source file, and a symlink survived. The shared root survived ordinary scratch pruning.
- The real cache closeout removed 137,258 old files, with 1,798,234,112 bytes of last-link file allocation. The exact candidate and removal manifests and filesystem measurements are in `/opt/data/cache/scratch/coordinator-cleanup-20261012/`.
- Existing Git worktree closeout retired `heysanta-posthog-20260930` and `heysanta-letters-integration-20261007` after refreshing remote ancestry and checking source and ignored files, processes, and enabled jobs. Unique evidence was moved unchanged into `/opt/data/artifacts/coordinator-closeout-20261012/` and rehashed before removal. Generated dist trees and build ZIPs were removed. Git removed the worktrees without force; their branches remain. Last-link file allocation removed from those trees was 763,355,136 bytes.

Total receipt-attributed last-link allocation removed: 2,561,589,248 bytes. Filesystem free-space changes are separate, concurrent measurements and are not the same accounting figure.

## Integration

Candidate source is isolated on `fix/coordinator-cache-closeout-20261012`, based on `f97608f178`. Before this edit, the scratch owner matched the active runtime predecessor. The parent owns integration and activation. The live `/opt/hermes` source and gateway were not changed or restarted. Existing loaded processes keep their predecessor code until normal activation or relaunch.

Grok's first proposal and its single focused correction both timed out without an HTTP response. The implementation was written natively; neither failed request is claimed as a completed proposal. The helper confirmed unchanged defaults and blocked an unrelated HTTP send.
