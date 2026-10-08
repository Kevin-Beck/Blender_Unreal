# Runs automatically when the editor starts (Unreal executes init_unreal.py from every plugin's Content/Python).
import unreal

if not unreal.SystemLibrary.is_unattended():  # headless harness runs call job_runner directly
    import unreal_link.queue_watcher as _ul_queue
    _ul_queue.register()
