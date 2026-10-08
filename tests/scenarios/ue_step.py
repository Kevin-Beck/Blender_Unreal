"""Unreal-side scenario helper: process the queue, optionally move a socket first.

    UnrealEditor-Cmd <project> -run=pythonscript -script="ue_step.py" ...
Environment: UL_SCENARIO_ACTION=move_socket to move 'Head_Helmet' by +5 cm Z before processing.
"""

import json
import os

import unreal
from unreal_link import lib, paths, queue_watcher, resolve
from unreal_link.core import jobs

p = paths()
action = os.environ.get("UL_SCENARIO_ACTION", "")
if action == "move_socket":
    guid = json.loads((p.root / "scenario_rig.json").read_text())["guid"]
    skel = resolve([guid])[guid]
    for s in json.loads(lib.get_skeleton_sockets(skel)):
        if s["name"] == "Head_Helmet":
            loc = s["location"]
            pitch, yaw, roll = s["rotation"]
            lib.set_skeleton_socket(skel, s["name"], s["bone"], unreal.Vector(loc[0], loc[1], loc[2] + 5.0),
                                    unreal.Rotator(roll, pitch, yaw), unreal.Vector(*s["scale"]))
    unreal.EditorAssetLibrary.save_loaded_asset(skel, only_if_is_dirty=False)

pending = jobs.queued(p)
queue_watcher.process_queue()
out = {}
for job_id in pending:
    r = jobs.read_result(p, job_id) or {}
    out[job_id] = {"status": r.get("status"), "error": r.get("error"),
                   "checks": [f"[{c['status']}] {c['msg']}" for c in r.get("checks", [])],
                   "kept": r.get("sockets_kept_unreal", [])}
(p.root / "scenario_ue.json").write_text(json.dumps(out, indent=2))
