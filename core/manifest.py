"""Manifest schema, the on-disk layout under <UEProject>/UnrealLink, and manifest history (design §3.1, §4.3)."""

import json
import os
import time
from pathlib import Path

from . import SCHEMA_VERSION
from .profile import PROFILE_ID

DEFAULT_CONFIG = {"fps": 30, "content_root": "/Game", "profile": PROFILE_ID}


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json(path, data):
    """Write atomically so a reader on the other side never sees a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=False)
    os.replace(tmp, path)


class Paths:
    """Everything the tool writes lives in <UEProject>/UnrealLink."""

    def __init__(self, ue_project_dir):
        self.project = Path(ue_project_dir)
        self.root = self.project / "UnrealLink"
        self.config = self.root / "config.json"
        self.manifests = self.root / "manifests"
        self.jobs = self.root / "jobs"
        self.queue = self.root / "queue"
        self.backups = self.root / "backups"
        self.lock = self.jobs / ".lock"

    def ensure(self):
        for d in (self.manifests, self.jobs, self.queue, self.backups):
            d.mkdir(parents=True, exist_ok=True)
        if not self.config.exists():
            write_json(self.config, DEFAULT_CONFIG)

    def load_config(self):
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(read_json(self.config, {}) or {})
        return cfg

    def job_dir(self, job_id):
        return self.jobs / job_id

    def history(self, asset_guid):
        return History(self.manifests / asset_guid)


class History:
    """Numbered manifest history for one rig.

    NNNN.json holds a verified (applied) manifest. history.json logs every attempt,
    including failed and rolled-back ones. Rolled-back revisions are renamed to
    NNNN.rolled_back.json so the highest NNNN.json is always the last verified one.
    """

    def __init__(self, folder):
        self.folder = Path(folder)
        self.log_path = self.folder / "history.json"

    def _revs(self):
        if not self.folder.is_dir():
            return []
        revs = []
        for p in self.folder.glob("[0-9][0-9][0-9][0-9].json"):
            revs.append(int(p.stem))
        return sorted(revs)

    def last_verified_rev(self):
        revs = self._revs()
        return revs[-1] if revs else 0

    def load(self, rev):
        return read_json(self.folder / f"{rev:04d}.json")

    def last_verified(self):
        rev = self.last_verified_rev()
        return (rev, self.load(rev)) if rev else (0, None)

    def next_rev(self):
        used = [e["rev"] for e in self.log()] + self._revs()
        return (max(used) if used else 0) + 1

    def log(self):
        return read_json(self.log_path, []) or []

    def record(self, rev, job_id, status, **extra):
        entries = [e for e in self.log() if not (e["rev"] == rev and e["job_id"] == job_id)]
        entries.append(dict(rev=rev, job_id=job_id, status=status, time=time.time(), **extra))
        entries.sort(key=lambda e: e["rev"])
        write_json(self.log_path, entries)

    def entry(self, rev):
        for e in reversed(self.log()):
            if e["rev"] == rev:
                return e
        return None

    def write_verified(self, rev, manifest):
        write_json(self.folder / f"{rev:04d}.json", manifest)

    def mark_rolled_back(self, rev):
        src = self.folder / f"{rev:04d}.json"
        if src.exists():
            os.replace(src, self.folder / f"{rev:04d}.rolled_back.json")


def validate(manifest):
    """Return a list of problems with a manifest (empty if it's usable)."""
    errs = []
    if not isinstance(manifest, dict):
        return ["manifest is not an object"]
    if manifest.get("schema") != SCHEMA_VERSION:
        errs.append(f"unsupported schema {manifest.get('schema')!r}")
    if manifest.get("profile") != PROFILE_ID:
        errs.append(f"unknown profile {manifest.get('profile')!r}")
    for key in ("asset_guid", "skeleton", "meshes", "sockets", "actions", "physics"):
        if key not in manifest:
            errs.append(f"missing '{key}'")
    if errs:
        return errs

    bones = manifest["skeleton"].get("bones", [])
    ids = {b["id"] for b in bones}
    if len(ids) != len(bones):
        errs.append("duplicate bone ids")
    live = [b for b in bones if not b.get("orphaned")]
    names = [b["name"] for b in live]
    if len(set(names)) != len(names):
        errs.append("duplicate bone names")
    roots = [b for b in live if b["parent"] is None]
    if live and len(roots) != 1:
        errs.append(f"expected one root bone, found {len(roots)}")
    for b in live:
        if b["parent"] is not None and b["parent"] not in ids:
            errs.append(f"bone {b['name']} has unknown parent {b['parent']}")
    for s in manifest["sockets"]:
        if s["bone_id"] not in ids:
            errs.append(f"socket {s['name']} is on unknown bone {s['bone_id']}")
    return errs


def live_bones(manifest):
    return [b for b in manifest["skeleton"]["bones"] if not b.get("orphaned")]


def bone_names_by_id(manifest):
    return {b["id"]: b["name"] for b in manifest["skeleton"]["bones"]}
