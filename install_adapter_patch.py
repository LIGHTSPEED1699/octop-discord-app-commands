#!/usr/bin/env python3
"""Apply the Discord application-command patch to the installed Octop adapter.

Idempotent and version-aware: the patch carries a version marker, so an older
patch is detected and replaced (restoring the pristine file from the backup
first, then applying the current patch). If an upstream Octop release changed
the file so the patch no longer applies, the failure is logged and the service
still starts - the feature degrades, the bot does not go down.

Called from ExecStartPre, so an `octop update` self-heals on the next start.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

PATCH_DIR = Path(__file__).resolve().parent
PATCH_FILE = PATCH_DIR / "discord_interaction.patch"
VERSION = "v2"
MARKER = f"octop-patch: discord-interaction-support {VERSION}"
SITE = Path("/home/hongbin/.octop/venv/lib/python3.12/site-packages")
TARGET = SITE / "harness_gateway" / "channels" / "discord.py"
BACKUP_DIR = PATCH_DIR / "backups"


def log(msg: str) -> None:
    print(f"[install-patch] {msg}", flush=True)


def newest_backup() -> Path | None:
    if not BACKUP_DIR.exists():
        return None
    backups = sorted(BACKUP_DIR.glob("discord.py.orig-*"))
    return backups[-1] if backups else None


def apply_patch() -> bool:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    pristine = newest_backup()
    if pristine is not None:
        shutil.copy2(pristine, TARGET)
        log(f"restored pristine base from {pristine.name}")
    else:
        backup = BACKUP_DIR / f"discord.py.orig-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(TARGET, backup)
        log(f"backed up original -> {backup.name}")
    r = subprocess.run(
        ["patch", "-p1", "--forward", "--batch", str(TARGET), str(PATCH_FILE)],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        log(f"patch FAILED (rc={r.returncode}): {(r.stdout or r.stderr).strip()[:300]}")
        log("the Discord '/' menu will keep showing only Discord's own commands")
        return False
    if MARKER not in TARGET.read_text():
        log("patch applied but marker missing - treating as failure")
        return False
    log(f"patch {VERSION} applied to {TARGET}")
    return True


def main() -> int:
    if not TARGET.exists():
        log(f"adapter not found at {TARGET} - nothing to do")
        return 0
    if MARKER in TARGET.read_text():
        log(f"patch {VERSION} already applied")
        return 0
    if not PATCH_FILE.exists():
        log(f"patch file missing: {PATCH_FILE}")
        return 0
    if "octop-patch: discord-interaction-support" in TARGET.read_text():
        log("an older patch version is installed - replacing it")
    apply_patch()
    return 0


if __name__ == "__main__":
    sys.exit(main())
