"""Process queued jobs (and pending rollbacks) once the editor has finished starting (design §10)."""

import unreal

from . import job_runner, paths
from .core import jobs

_handle = None


def process_queue():
    p = paths()
    items = jobs.queued(p)
    # Pending rollbacks first: they must run before anything loads the assets again.
    for name in sorted(items, key=lambda n: (not n.startswith("rollback_"), n)):
        if name.startswith("rollback_"):
            job_id = name[len("rollback_"):]
            ok = job_runner.rollback(job_id)
            unreal.log(f"Unreal Link: rollback of {job_id} {'done' if ok else 'failed'}")
        else:
            status = job_runner.run(name)
            msg = f"Unreal Link: queued job {name} → {status}"
            (unreal.log if status == jobs.VERIFIED else unreal.log_warning)(msg)
    return len(items)


def _tick(_delta):
    global _handle
    reg = unreal.AssetRegistryHelpers.get_asset_registry()
    if reg.is_loading_assets():
        return
    unreal.unregister_slate_post_tick_callback(_handle)
    _handle = None
    try:
        n = process_queue()
        if n:
            unreal.log(f"Unreal Link: processed {n} queued item(s)")
    except Exception as e:
        unreal.log_error(f"Unreal Link: queue processing failed: {e}")


def register():
    global _handle
    if _handle is None:
        _handle = unreal.register_slate_post_tick_callback(_tick)
