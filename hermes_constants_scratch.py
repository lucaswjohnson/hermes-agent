"""Scratch-dir retention: idle detection plus the reaping an idle tree needs before it goes.

Deleting an idle ``cache/scratch/<entry>`` is not enough on its own: a lane's e2e run leaves
headless browsers whose cwd was inside the tree (they survived for days with a ``(deleted)``
cwd), and a repo whose linked worktree lived in the tree keeps a dangling registration until
someone runs ``git worktree prune``. Both are reaped here, right before ``rmtree``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# How long a TERMed process gets before KILL; browsers exit well within this.
_REAP_GRACE_SECONDS = 3.0
# ``.git`` files (linked worktrees) are looked for this deep; lanes nest repo/tree/subtree.
_GIT_FILE_MAX_DEPTH = 4


def subtree_touched_since(path: Path, cutoff: float) -> bool:
    """True when *path* or anything beneath it has an mtime at or after *cutoff*.

    Stops at the first recent entry, so a live tree costs one hit and only a truly idle
    tree pays for the full walk (once, right before it is deleted). Symlinks are never
    followed: a link into the repo would make the target's activity keep the entry alive.
    An unreadable entry is kept: an incomplete scan cannot establish that it is idle.
    """
    try:
        if os.lstat(path).st_mtime >= cutoff:
            return True
        if not path.is_dir() or path.is_symlink():
            return False
    except OSError:
        return True
    stack = [str(path)]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for child in it:
                    try:
                        if child.stat(follow_symlinks=False).st_mtime >= cutoff:
                            return True
                    except OSError:
                        return True
                    if child.is_dir(follow_symlinks=False):
                        stack.append(child.path)
        except OSError:
            return True
    return False


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _own_lineage() -> set[int]:
    """This process and its ancestors: never reap the shell that is running the prune."""
    import psutil

    pids: set[int] = set()
    try:
        proc = psutil.Process()
        while proc is not None and proc.pid not in pids:
            pids.add(proc.pid)
            proc = proc.parent()
    except (psutil.Error, OSError):
        pass
    return pids


def reap_processes_rooted_in(scratch_root: Path, doomed: list[Path]) -> int:
    """TERM (then KILL) same-user processes whose cwd is inside an entry about to be
    pruned, or is a path under *scratch_root* that no longer exists. Returns the count.

    Only the cwd is consulted: a process merely holding a file open inside scratch (an
    editor, a log tail) is not ours to kill, but one *living* in a directory we are
    about to delete, or in one already gone, has nothing left to run for.
    """
    import psutil

    root = os.path.realpath(str(scratch_root))
    targets = [os.path.realpath(str(p)) for p in doomed]
    skip = _own_lineage()
    uid = os.getuid() if hasattr(os, "getuid") else None
    victims: list[psutil.Process] = []
    for proc in psutil.process_iter(["pid"]):
        if proc.pid in skip:
            continue
        try:
            if uid is not None and proc.uids().real != uid:
                continue
            cwd = proc.cwd()
        except (psutil.Error, OSError):
            continue
        if not cwd:
            continue
        deleted = cwd.endswith(" (deleted)")
        cwd_path = cwd[: -len(" (deleted)")] if deleted else cwd
        if not _under(cwd_path, root):
            continue
        if deleted or not os.path.exists(cwd_path) or any(_under(cwd_path, t) for t in targets):
            victims.append(proc)
    if not victims:
        return 0
    for proc in victims:
        try:
            proc.terminate()
        except (psutil.Error, OSError):
            continue
    _, alive = psutil.wait_procs(victims, timeout=_REAP_GRACE_SECONDS)
    for proc in alive:
        try:
            proc.kill()
        except (psutil.Error, OSError):
            continue
    logger.info("scratch prune: reaped %d process(es) rooted in pruned entries", len(victims))
    return len(victims)


def _linked_worktree_repos(entry: Path) -> set[str]:
    """Repos whose linked worktrees live inside *entry* (``.git`` FILES, ``gitdir: <repo>/.git/worktrees/<n>``)."""
    repos: set[str] = set()
    stack = [(str(entry), 0)]
    while stack:
        current, depth = stack.pop()
        try:
            with os.scandir(current) as it:
                for child in it:
                    if child.name == ".git" and child.is_file(follow_symlinks=False):
                        try:
                            line = Path(child.path).read_text(encoding="utf-8", errors="replace").strip()
                        except OSError:
                            continue
                        if line.startswith("gitdir:"):
                            gitdir = Path(line[len("gitdir:"):].strip())
                            # <repo>/.git/worktrees/<name> -> <repo>
                            if gitdir.parent.name == "worktrees" and gitdir.parent.parent.name == ".git":
                                repos.add(str(gitdir.parent.parent.parent))
                    elif child.is_dir(follow_symlinks=False) and depth < _GIT_FILE_MAX_DEPTH \
                            and child.name not in ("node_modules", ".venv", "venv"):
                        stack.append((child.path, depth + 1))
        except OSError:
            continue
    return repos


def release_git_worktrees(repos: set[str]) -> None:
    """``git worktree prune`` in each repo: drops registrations whose tree we just deleted."""
    for repo in sorted(repos):
        if not os.path.isdir(repo):
            continue
        try:
            subprocess.run(
                ["git", "-C", repo, "worktree", "prune"],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=15, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("git worktree prune in %s failed: %s", repo, exc)


def _metro_cache_in_use(cache: Path) -> bool:
    """Hold the shared cache for consumers; never kill them to make room.

    Only same-user processes can own this user's private scratch cache. An
    incomplete inspection of one of those processes holds the entire cache.
    """
    marker = str(cache.absolute())
    consumer = re.compile(r"(?:^|[ /\\\\])(?:metro|expo)(?:[- /\\\\.]|$)")
    try:
        import psutil

        jobs_file = cache.parents[2] / "cron" / "jobs.json"
        if jobs_file.exists():
            data = json.loads(jobs_file.read_text(encoding="utf-8"))
            jobs = data.get("jobs", []) if isinstance(data, dict) else data
            if not isinstance(jobs, list):
                return True
            for job in jobs:
                if not isinstance(job, dict):
                    return True
                if job.get("enabled", True) and not job.get("paused", False):
                    payload = json.dumps(job)
                    if marker in payload or consumer.search(payload):
                        return True
        skip = _own_lineage()
        for proc in psutil.process_iter():
            if proc.pid in skip:
                continue
            try:
                if proc.uids().real != os.getuid():
                    continue
                command = " ".join(proc.cmdline())
                if marker in command or consumer.search(command):
                    return True
                paths = [proc.cwd(), proc.exe()]
                paths.extend(f.path for f in proc.open_files())
                paths.extend(m.path for m in proc.memory_maps(grouped=True))
                if any(_under(p.removesuffix(" (deleted)"), marker) for p in paths):
                    return True
            except psutil.NoSuchProcess:
                continue
            except (psutil.Error, OSError):
                return True
    except (ImportError, OSError, ValueError):
        return True
    return False


def prune_metro_cache(cache: Path, cutoff: float) -> dict[str, int]:
    """Evict old generated Metro leaves, not the shared tree or unknown files.

    A recent leaf in one shard must not retain all older leaves. Root and shard
    handles do not follow symlinks. Return last-link allocation separately from
    the filesystem's concurrently changing free space.
    """
    result = {"removed_files": 0, "last_link_allocated_bytes": 0}
    if cache.name != "metro-cache" or cache.is_symlink() or not cache.is_dir():
        return result
    if os.name != "posix" or _metro_cache_in_use(cache):
        return result
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        with os.scandir(cache) as entries:
            shards = [e.name for e in entries if re.fullmatch(r"[0-9a-f]{2}", e.name)]
        root_fd = os.open(cache, flags)
    except OSError:
        return result
    try:
        for shard in shards:
            try:
                shard_fd = os.open(shard, flags, dir_fd=root_fd)
            except OSError:
                continue
            try:
                with os.scandir(shard_fd) as leaves:
                    candidates = [(e.name, e.stat(follow_symlinks=False)) for e in leaves
                                  if re.fullmatch(r"[0-9a-f]{64,128}", e.name)]
                for name, before in candidates:
                    if not stat.S_ISREG(before.st_mode) or before.st_mtime >= cutoff:
                        continue
                    current = os.stat(name, dir_fd=shard_fd, follow_symlinks=False)
                    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
                    if identity(current) != identity(before) or current.st_mtime >= cutoff:
                        continue
                    os.unlink(name, dir_fd=shard_fd)
                    result["removed_files"] += 1
                    if current.st_nlink == 1:
                        result["last_link_allocated_bytes"] += current.st_blocks * 512
            except OSError:
                continue
            finally:
                os.close(shard_fd)
    finally:
        os.close(root_fd)
    if result["removed_files"]:
        logger.info("scratch Metro cache closeout: %s", result)
    return result


def prune_idle_entries(root: Path, max_idle_hours: float, skip_names: frozenset[str]) -> int:
    """Delete top-level entries of *root* with no write anywhere in their subtree for
    *max_idle_hours*, reaping processes and worktree registrations rooted in them first.
    Returns the number of top-level entries and generated cache leaves removed.
    The shared Metro cache is handled leaf-by-leaf and never recursively removed.
    """
    cutoff = time.time() - max_idle_hours * 3600
    cache_removed = 0
    if "metro-cache" not in skip_names:
        cache_removed = prune_metro_cache(root / "metro-cache", cutoff)["removed_files"]
    try:
        entries = [e for e in root.iterdir() if e.name not in skip_names and e.name != "metro-cache"]
    except OSError:
        return cache_removed
    doomed = [e for e in entries if not subtree_touched_since(e, cutoff)]
    # Runs even with nothing to delete: orphans whose cwd was removed by an earlier pass
    # (or by hand) are found by the deleted-cwd rule, not by membership in ``doomed``.
    try:
        reap_processes_rooted_in(root, doomed)
    except Exception as exc:  # psutil missing or restricted host: the deletion still proceeds
        logger.debug("scratch prune: process reap skipped: %s", exc)
    if not doomed:
        return cache_removed
    repos: set[str] = set()
    removed = cache_removed
    for entry in doomed:
        try:
            if entry.is_dir() and not entry.is_symlink():
                repos |= _linked_worktree_repos(entry)
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink()
            removed += 1
        except OSError:
            continue
    release_git_worktrees(repos)
    return removed
