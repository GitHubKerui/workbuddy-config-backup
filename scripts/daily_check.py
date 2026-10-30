#!/usr/bin/env python3
"""每天只跑一次备份，供开机启动项和定时任务共用。

为什么需要这个守卫：开机项和定时任务两条路径都会触发，
没有"当天已跑过"的记录就会重复产生空提交。
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

import common


def main() -> int:
    common.main_guard()
    cfg = common.require_config()
    if cfg is None:
        return 1

    stamp = common.wb_root(cfg) / "backup-last-check.stamp"
    log = common.wb_root(cfg) / "backup-last-run.log"
    today = date.today().isoformat()

    if stamp.exists() and stamp.read_text(encoding="utf-8").strip() == today:
        return 0

    result = subprocess.run(
        [sys.executable, str(common.SKILL_DIR / "scripts" / "backup.py")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    output = (result.stdout or "") + (result.stderr or "")

    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"--- {today} ---\n{output}\n")

    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(today, encoding="utf-8")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
