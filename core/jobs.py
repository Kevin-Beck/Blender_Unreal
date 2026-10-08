"""Job folder and result.json helpers shared by both sides (design §3.1, §5 Send, §9.1)."""

import os
import secrets
import time

from . import SCHEMA_VERSION
from .manifest import read_json, write_json

RUNNING, VERIFIED, FAILED, ROLLED_BACK, QUEUED = "running", "verified", "failed", "rolled_back", "queued"


def new_job_id():
    # Time first so queue order is creation order.
    return time.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(2)


def build_job(job_id, manifest, base_rev, rev, changeset, files, confirmations, socket_policy, blender_file):
    """The job.json contents. The ops are advisory: Unreal re-diffs and fails on mismatch."""
    return {
        "schema": SCHEMA_VERSION,
        "job_id": job_id,
        "asset_guid": manifest["asset_guid"],
        "base_rev": base_rev,
        "rev": rev,
        "created": time.time(),
        "blender_file": blender_file,
        "first_import": changeset.first_import,
        "files": files,
        "ops": ops_from_changeset(changeset),
        "confirmations": confirmations,
        "socket_policy": socket_policy,
    }


def ops_from_changeset(cs):
    return {
        "bone_renames": sorted(cs.renames("bone").items()),
        "socket_renames": sorted(cs.renames("socket").items()),
        "slot_renames": sorted(cs.renames("slot").items()),
        "removed_bones": sorted(cs.removed("bone")),
        "removed_sockets": sorted(cs.removed("socket")),
        "removed_slots": sorted(cs.removed("slot")),
        "removed_actions": sorted(c.id for c in cs.of("action") if c.status == "removed"),
    }


def write_job(paths, job, manifest):
    d = paths.job_dir(job["job_id"])
    d.mkdir(parents=True, exist_ok=True)
    write_json(d / "manifest.json", manifest)
    write_json(d / "job.json", job)
    return d


def enqueue(paths, name):
    paths.queue.mkdir(parents=True, exist_ok=True)
    (paths.queue / name).write_text(str(time.time()), encoding="utf-8")


def queued(paths):
    if not paths.queue.is_dir():
        return []
    return sorted(p.name for p in paths.queue.iterdir() if not p.name.startswith("."))


def dequeue(paths, name):
    try:
        (paths.queue / name).unlink()
    except FileNotFoundError:
        pass


def read_result(paths, job_id):
    return read_json(paths.job_dir(job_id) / "result.json")


class Result:
    """result.json writer used by the Unreal job runner. Every change is flushed to disk
    so Blender can show live progress."""

    def __init__(self, paths, job_id, rev=None):
        self.path = paths.job_dir(job_id) / "result.json"
        self.started = time.time()
        self.data = read_json(self.path) or {
            "job_id": job_id, "rev": rev, "status": RUNNING, "started": self.started,
            "finished": None, "duration": None, "progress": [], "checks": [],
            "dependency_index": {}, "sockets_unreal": {}, "sockets_kept_unreal": [],
            "manual_review": [], "error": None,
        }
        self.data["status"] = RUNNING
        self.flush()

    def flush(self):
        write_json(self.path, self.data)

    def progress(self, step, total, msg):
        self.data["progress"].append({"t": time.time(), "step": step, "total": total, "msg": msg})
        self.flush()

    def check(self, check_id, status, msg):
        self.data["checks"].append({"id": check_id, "status": status, "msg": msg})

    def finish(self, status, error=None):
        self.data["status"] = status
        self.data["error"] = error
        self.data["finished"] = time.time()
        self.data["duration"] = round(self.data["finished"] - self.started, 1)
        self.flush()


class Lock:
    """One job at a time per project. A lock older than an hour is treated as stale."""

    def __init__(self, path, stale_after=3600):
        self.path = path
        self.stale_after = stale_after

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and time.time() - self.path.stat().st_mtime > self.stale_after:
            self.path.unlink()
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise RuntimeError("another Unreal Link job is running (jobs/.lock exists)")
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return self

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
