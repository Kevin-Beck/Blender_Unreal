#!/usr/bin/env bash
# Wipe everything Unreal Link created in a test project so a scenario starts from scratch.
set -euo pipefail
P="${1:?usage: ue_reset.sh <UEProjectDir>}"
rm -rf "$P/UnrealLink" "$P/Content/Characters"
