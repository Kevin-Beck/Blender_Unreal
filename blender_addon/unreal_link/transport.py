"""Live (remote execution) and queued transport, plus live queries (design §10)."""

import ast
import json
import os
import threading
import time

from . import prefs, remote_exec
from .core import jobs

LIVE, QUEUED, NONE = "LIVE", "QUEUED", "NONE"
CHECK_INTERVAL = 10.0


class _Status:
    def __init__(self):
        self.mode = NONE
        self.node_id = None
        self.engine_version = ""
        self.checked = 0.0
        self.checking = False
        self.last_draw = 0.0


status = _Status()
_client = None
_client_key = None
_lock = threading.Lock()

# job_id -> {"done": bool, "error": str|None}
live_jobs = {}


def client():
    global _client, _client_key
    p = prefs.get()
    key = (p.multicast_group, p.multicast_port, p.multicast_bind)
    if _client is None or key != _client_key:
        if _client:
            _client.close()
        _client = remote_exec.Client(remote_exec.Config(
            group=p.multicast_group, port=p.multicast_port, bind=p.multicast_bind))
        _client_key = key
    return _client


def _same_dir(a, b):
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except OSError:
        return False


def _check():
    paths = prefs.paths()
    mode, node, version = NONE, None, ""
    if paths is not None:
        mode = QUEUED
        try:
            with _lock:
                nodes = client().discover(timeout=1.0)
        except OSError:
            nodes = {}
        for nid, data in nodes.items():
            if _same_dir(data.get("project_root", ""), paths.project):
                mode, node, version = LIVE, nid, data.get("engine_version", "")
                break
    status.mode, status.node_id, status.engine_version = mode, node, version
    status.checked = time.time()
    status.checking = False


def poll(force=False):
    """Kick off a discovery ping in the background if one is due."""
    if status.checking:
        return
    if prefs.paths() is None:
        status.mode = NONE
        return
    if force or time.time() - status.checked > CHECK_INTERVAL:
        status.checking = True
        threading.Thread(target=_check, daemon=True).start()


def is_live():
    return status.mode == LIVE and status.node_id is not None


def send_job(paths, job_id):
    """Start a job: live if the editor is reachable, otherwise queue it. Returns the mode used."""
    if is_live():
        live_jobs[job_id] = {"done": False, "error": None}

        def work():
            try:
                with _lock:
                    res = client().run(status.node_id,
                                       f"import unreal_link.job_runner as r; r.run({job_id!r})",
                                       timeout=None)
                if not res.get("success"):
                    live_jobs[job_id]["error"] = remote_exec.output_text(res) or res.get("result")
            except Exception as e:  # network errors land in the UI, not the console
                live_jobs[job_id]["error"] = str(e)
            live_jobs[job_id]["done"] = True

        threading.Thread(target=work, daemon=True).start()
        return LIVE
    jobs.enqueue(paths, job_id)
    return QUEUED


def request_rollback(paths, job_id):
    if is_live():
        run_statement(f"import unreal_link.job_runner as r; r.rollback({job_id!r})")
        return LIVE
    jobs.enqueue(paths, f"rollback_{job_id}")
    return QUEUED


def run_statement(stmt, timeout=60.0):
    with _lock:
        res = client().run(status.node_id, stmt, remote_exec.EXEC_STATEMENT, timeout=timeout)
    if not res.get("success"):
        raise remote_exec.RemoteError(remote_exec.output_text(res) or str(res.get("result")))
    return res


def query(func, *args, timeout=30.0):
    """Call unreal_link.query.<func>(*args) in the editor. Query functions return JSON strings."""
    if not is_live():
        raise remote_exec.RemoteError("the Unreal editor isn't live")
    arg_src = ", ".join(repr(a) for a in args)
    expr = f"__import__('unreal_link.query', fromlist=['query']).{func}({arg_src})"
    with _lock:
        res = client().run(status.node_id, expr, remote_exec.EVAL_STATEMENT, timeout=timeout)
    if not res.get("success"):
        raise remote_exec.RemoteError(remote_exec.output_text(res) or str(res.get("result")))
    value = res.get("result")
    try:
        value = ast.literal_eval(value)
    except (ValueError, SyntaxError):
        pass
    return json.loads(value) if isinstance(value, str) else value
