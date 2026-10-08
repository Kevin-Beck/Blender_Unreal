"""Headless Unreal harness: process everything in <project>/UnrealLink/queue and print the results.

    UnrealEditor-Cmd <project>.uproject -run=pythonscript -script="<repo>/tests/ue_process_queue.py" -unattended -nullrhi
"""

import json

import unreal
from unreal_link import paths, queue_watcher
from unreal_link.core import jobs

p = paths()
pending = jobs.queued(p)
queue_watcher.process_queue()
report = []
for name in pending:
    job_id = name[len("rollback_"):] if name.startswith("rollback_") else name
    r = jobs.read_result(p, job_id) or {}
    lines = [f"UL_RESULT {job_id}: {r.get('status')} ({r.get('duration')} s)"]
    lines += [f"UL_RESULT   [{c['status']}] {c['msg']}" for c in r.get("checks", [])]
    if r.get("error"):
        lines.append(f"UL_RESULT   error: {r['error']}")
    report += lines
(p.root / "last_report.txt").write_text("\n".join(report) + "\n")
unreal.log("\n".join(report))
