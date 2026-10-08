"""Run the design §11 reimport scenarios end to end against a real Unreal project.

    python tests/scenarios/run_scenarios.py <UEProjectDir> [--blender PATH] [--ue PATH]

Each step changes the knight in Blender and sends it, then a headless Unreal processes the job.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

# (step, expect_blocked, ue_action, expectations on the Blender change set)
STEPS = [
    ("first", False, "", {}),
    ("rename", False, "", {"remaps": 4}),
    ("add_bone", False, "", {}),
    ("remove_bone", False, "", {"destructive": 1}),
    ("resend", False, "move_socket", {}),
    ("reparent", True, "", {}),
]


def run(cmd, env=None):
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--blender", default=str(Path.home() / "dev/tools/blender-5.2.2-linux-x64/blender"))
    ap.add_argument("--ue", default=str(Path.home() / "dev/tools/UnrealEngine-5.8.3/Engine/Binaries/Linux/UnrealEditor-Cmd"))
    a = ap.parse_args()
    project = Path(a.project).resolve()
    uproject = next(project.glob("*.uproject"))
    subprocess.run([str(REPO / "tests" / "ue_reset.sh"), str(project)], check=True)
    work = project / "Saved" / "scenario_knight.blend"
    work.parent.mkdir(exist_ok=True)
    shutil.copy(REPO / "tests" / "golden" / "knight.blend", work)

    failures = 0
    for step, expect_blocked, ue_action, expect in STEPS:
        b = run([a.blender, "-b", str(work), "--python", str(HERE / "blender_step.py"), "--", str(project), step])
        line = next((l for l in b.stdout.splitlines() if l.startswith("STEP ")), None)
        if line is None:
            print(f"✗ {step}: Blender step crashed\n{b.stdout[-3000:]}\n{b.stderr[-2000:]}")
            return 1
        info = json.loads(line[5:])
        if step == "first":
            job = json.loads((project / "UnrealLink" / "jobs" / info["job"] / "job.json").read_text())
            (project / "UnrealLink" / "scenario_rig.json").write_text(json.dumps({"guid": job["asset_guid"]}))
        ok = True
        msgs = [f"remaps={info['remaps']} destructive={info['destructive']} blocked={info['blocked']}"]
        for key, n in expect.items():
            if len(info[key]) != n:
                ok = False
                msgs.append(f"expected {n} {key}, got {len(info[key])}")
        if expect_blocked:
            ok = ok and bool(info["blocked"]) and not info.get("sent")
            print(f"{'✓' if ok else '✗'} {step}: {'blocked as expected' if ok else 'NOT blocked'}; " + "; ".join(msgs))
            failures += not ok
            continue
        if not info.get("sent"):
            print(f"✗ {step}: not sent; blocking={info['blocking']}")
            return 1
        env = dict(os.environ, UL_SCENARIO_ACTION=ue_action)
        # Unreal leaves a zen cache daemon running that inherits pipes, so log to a file instead.
        with open(project / "Saved" / f"scenario_{step}.log", "w") as log:
            subprocess.run([a.ue, str(uproject), "-run=pythonscript", f"-script={HERE / 'ue_step.py'}",
                            "-unattended", "-nullrhi", "-nosplash", "-stdout"], env=env, stdout=log,
                           stderr=subprocess.STDOUT)
        res = json.loads((project / "UnrealLink" / "scenario_ue.json").read_text()).get(info["job"], {})
        ok = ok and res.get("status") == "verified"
        if ue_action == "move_socket":
            ok = ok and bool(res.get("kept"))
            msgs.append(f"kept Unreal socket edits: {res.get('kept')}")
        print(f"{'✓' if ok else '✗'} {step}: {res.get('status')} " + "; ".join(msgs))
        if not ok:
            for c in res.get("checks", []):
                print("     " + c)
            if res.get("error"):
                print("     error: " + str(res["error"]))
        failures += not ok
    print(f"{len(STEPS) - failures}/{len(STEPS)} scenarios passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
