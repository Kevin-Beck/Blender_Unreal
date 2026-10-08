"""Install Unreal Link.

    python tools/install.py blender [--version 5.2]       copy the add-on into Blender's user extensions
    python tools/install.py zip                           build dist/unreal_link.zip (Install from Disk)
    python tools/install.py unreal <UEProjectDir>         copy the plugin into <project>/Plugins and
                                                          turn on Python remote execution

The shared core is synced into both sides first.
"""

import argparse
import os
import shutil
import sys
import zipfile
from pathlib import Path

from sync_core import REPO, sync

ADDON = REPO / "blender_addon" / "unreal_link"
PLUGIN = REPO / "unreal_plugin" / "UnrealLink"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "Binaries", "Intermediate")


def blender_extensions_dir(version):
    if sys.platform == "win32":
        base = Path(os.environ["APPDATA"]) / "Blender Foundation" / "Blender"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Blender"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "blender"
    return base / version / "extensions" / "user_default"


def install_blender(version):
    dest = blender_extensions_dir(version) / "unreal_link"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(ADDON, dest, ignore=IGNORE)
    print(f"add-on -> {dest}\nEnable it in Blender: Preferences > Get Extensions / Add-ons > Unreal Link")


def build_zip():
    out = REPO / "dist" / "unreal_link.zip"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in ADDON.rglob("*"):
            if f.is_file() and "__pycache__" not in f.parts:
                z.write(f, f.relative_to(ADDON))
    print(f"extension zip -> {out}")


REMOTE_EXEC = ("[/Script/PythonScriptPlugin.PythonScriptPluginSettings]\n"
               "bRemoteExecution=True\n")


def install_unreal(project):
    project = Path(project).resolve()
    if not list(project.glob("*.uproject")):
        sys.exit(f"no .uproject in {project}")
    dest = project / "Plugins" / "UnrealLink"
    if dest.exists():
        # Keep compiled binaries so a Python-only update doesn't force a rebuild.
        for item in dest.iterdir():
            if item.name not in ("Binaries", "Intermediate"):
                shutil.rmtree(item) if item.is_dir() else item.unlink()
    shutil.copytree(PLUGIN, dest, ignore=IGNORE, dirs_exist_ok=True)
    print(f"plugin -> {dest}")

    ini = project / "Config" / "DefaultEngine.ini"
    ini.parent.mkdir(exist_ok=True)
    text = ini.read_text(encoding="utf-8") if ini.exists() else ""
    if "bRemoteExecution" not in text:
        ini.write_text(text.rstrip() + "\n\n" + REMOTE_EXEC, encoding="utf-8")
        print(f"remote execution enabled in {ini}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("blender")
    b.add_argument("--version", default="5.2")
    sub.add_parser("zip")
    u = sub.add_parser("unreal")
    u.add_argument("project")
    args = ap.parse_args()

    sync()
    if args.cmd == "blender":
        install_blender(args.version)
    elif args.cmd == "zip":
        build_zip()
    else:
        install_unreal(args.project)


if __name__ == "__main__":
    main()
