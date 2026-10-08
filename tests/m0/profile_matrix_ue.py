"""M0: process the queued variant jobs and report root transform, bounds and animation facts.

    UnrealEditor-Cmd <project>.uproject -run=pythonscript -script="<repo>/tests/m0/profile_matrix_ue.py" -unattended -nullrhi
"""

import json

import unreal
from unreal_link import lib, paths, queue_watcher, resolve
from unreal_link.core import jobs

p = paths()
variants = json.loads((p.root / "m0_variants.json").read_text())
rows = []
for name, job_id in variants.items():
    r = jobs.read_result(p, job_id) or {}
    m = json.loads((p.job_dir(job_id) / "manifest.json").read_text())
    found = resolve([m["meshes"][0]["asset_guid"]] + [a["asset_guid"] for a in m["actions"]])
    mesh = found.get(m["meshes"][0]["asset_guid"])
    info = json.loads(lib.get_mesh_info(mesh)) if mesh else {}
    anims = [json.loads(lib.get_anim_info(found[a["asset_guid"]])) for a in m["actions"] if a["asset_guid"] in found]
    rows.append(f"M0 {name:10} status={r.get('status')} bones={[b['name'] for b in info.get('bones', [])]} "
                f"root_scale={info.get('root_scale')} root_rot={[round(v, 2) for v in info.get('root_rotation', [])]} "
                f"root_loc={[round(v, 2) for v in info.get('root_location', [])]} "
                f"bounds={[round(v, 1) for v in info.get('bounds_size', [])]} want={m['meshes'][0]['bounds_size_cm']} "
                f"anim_frames={[a.get('num_frames') for a in anims]} "
                f"fails={[c['msg'] for c in r.get('checks', []) if c['status'] == 'fail']} err={r.get('error')}")
(p.root / "m0_report.txt").write_text("\n".join(rows) + "\n")
unreal.log("M0 report written to " + str(p.root / "m0_report.txt"))
