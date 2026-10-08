"""Copy the canonical shared core into the Blender add-on and the Unreal plugin.

Run after editing anything under <repo>/core:  python tools/sync_core.py
"""

import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "core"
TARGETS = [
    REPO / "blender_addon" / "unreal_link" / "core",
    REPO / "unreal_plugin" / "UnrealLink" / "Content" / "Python" / "unreal_link" / "core",
]


def sync():
    for dst in TARGETS:
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        print(f"core -> {dst.relative_to(REPO)}")


if __name__ == "__main__":
    sync()
