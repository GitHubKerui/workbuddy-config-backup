#!/usr/bin/env python3
"""从备份仓库恢复到本机。

为什么默认不覆盖：这些文件客户端正在使用，误覆盖会丢当前记忆或规则。
所以先 --dry-run 看清单，确认后再 --force；覆盖前原文件自动归档。

换电脑要处理的三件事：
1. 配置目录可能不存在 → 自动创建；personal 目录带 uid，没登录过就从仓库里反推。
2. 项目路径是旧机器的绝对路径 → --map 旧前缀=新前缀。
3. 仓库还没拉下来 → 交给 bootstrap.py。

用法
----
    python scripts/restore.py --dry-run
    python scripts/restore.py --force
    python scripts/restore.py --force --map "D:/projects=E:/AIDev"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import common

SKIP_RELS = {"MANIFEST.json", "README.md", ".gitignore", "projects/index.json"}


def resolve_personal(wb: Path, repo: Path, override: str | None) -> Path | None:
    """定位 user-<uid>-personal。

    优先级：显式指定 > 本机已存在 > 从仓库记忆文件名推导 uid。
    为什么要推导：换电脑首次恢复时客户端还没登录，目录还不存在，
    但仓库里的 <uid>_memory.md 文件名正好带着 uid。
    """
    if override:
        return Path(override).expanduser()
    existing = sorted(wb.glob("user-*-personal"))
    if existing:
        return existing[0]
    for candidate in sorted((repo / "workbuddy" / "memory").glob("*_memory.md")):
        match = common.UID_RE.search(candidate.name)
        if match:
            return wb / f"user-{match.group(1)}-personal"
    return None


def parse_maps(pairs: list[str]) -> list[tuple[str, str]]:
    maps: list[tuple[str, str]] = []
    for raw in pairs:
        if "=" not in raw:
            raise SystemExit(f"--map 格式应为 旧前缀=新前缀，收到：{raw}")
        old, new = raw.split("=", 1)
        maps.append((old.rstrip("/\\"), new.rstrip("/\\")))
    return maps


def remap(raw: str, maps: list[tuple[str, str]]) -> str:
    normalized = raw.replace("\\", "/")
    for old, new in maps:
        old_n = old.replace("\\", "/")
        if normalized.lower().startswith(old_n.lower()):
            return new + normalized[len(old_n):]
    return raw


def dest_for(rel, repo, wb, cb, personal, index, maps) -> Path | None:
    if rel in SKIP_RELS:
        return None
    if rel.startswith("projects/"):
        parts = rel.split("/", 2)
        if len(parts) < 3 or parts[1] not in index:
            return None
        base = Path(remap(index[parts[1]], maps))
        # Windows 上无盘符路径会被解释成"当前盘根目录"，会悄悄写到 C:\data 这类地方
        if os.name == "nt" and not base.drive:
            return None
        return base / parts[2]
    if rel.startswith("workbuddy/identity/account/"):
        return wb / rel[len("workbuddy/identity/account/"):]
    if rel.startswith("workbuddy/identity/personal/"):
        return (personal / rel[len("workbuddy/identity/personal/"):]) if personal else None
    if rel == "workbuddy/config/app-config.json":
        return wb / "app" / "app-config.json"
    if rel.startswith("workbuddy/config/"):
        return wb / rel[len("workbuddy/config/"):]
    if rel.startswith("workbuddy/"):
        return wb / rel[len("workbuddy/"):]
    if rel.startswith("codebuddy/memory/"):
        return cb / "memery" / rel[len("codebuddy/memory/"):]
    if rel.startswith("codebuddy/config/"):
        return cb / rel[len("codebuddy/config/"):]
    if rel.startswith("codebuddy/"):
        return cb / rel[len("codebuddy/"):]
    return None


def build_plan(repo: Path, wb: Path, cb: Path, maps, personal_override=None):
    manifest = json.loads((repo / "MANIFEST.json").read_text(encoding="utf-8"))
    index_file = repo / "projects" / "index.json"
    index = json.loads(index_file.read_text(encoding="utf-8")) if index_file.exists() else {}
    personal = resolve_personal(wb, repo, personal_override)

    plan, skipped = [], []
    for rel in sorted(manifest.get("files", {})):
        dest = dest_for(rel, repo, wb, cb, personal, index, maps)
        if dest is None:
            skipped.append(rel)
            continue
        plan.append((repo / rel, dest))
    return plan, skipped, personal


def apply_plan(plan, wb: Path, force: bool) -> int:
    if not force:
        return 0
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive = wb / f"restore-backup-{stamp}"
    written = 0
    for src, dest in plan:
        if not src.exists():
            print(f"  ! 源缺失 {src}")
            continue
        if dest.exists():
            saved = archive / str(dest).replace(":", "").lstrip("\\/")
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, saved)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        written += 1
    if written:
        print(f"原文件归档：{archive}")
    return written


def main(argv: list[str] | None = None) -> int:
    common.main_guard()
    parser = argparse.ArgumentParser(description="从备份恢复私有配置")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--wb-dir")
    parser.add_argument("--cb-dir")
    parser.add_argument("--personal-dir")
    parser.add_argument("--map", action="append", default=[], metavar="OLD=NEW")
    args = parser.parse_args(argv)

    cfg = common.require_config()
    if cfg is None:
        return 1
    repo = common.repo_path(cfg)
    if not (repo / "MANIFEST.json").exists():
        print(f"仓库里还没有备份数据：{repo}\n先执行 python scripts/backup.py")
        return 1

    wb = Path(args.wb_dir).expanduser() if args.wb_dir else common.wb_root(cfg)
    cb = Path(args.cb_dir).expanduser() if args.cb_dir else common.cb_root(cfg)
    plan, skipped, personal = build_plan(repo, wb, cb, parse_maps(args.map), args.personal_dir)

    print(f"仓库：{repo}")
    print(f"WorkBuddy 目录：{wb}")
    print(f"CodeBuddy 目录：{cb}")
    print(f"个人目录：{personal if personal else '未找到（跳过 identity/personal）'}")
    print(f"待恢复 {len(plan)} 个文件，跳过 {len(skipped)} 个")
    lost = [r for r in skipped if r.startswith("projects/")]
    if lost:
        print(f"  ! {len(lost)} 个项目文件路径无法定位，跨机器请加 --map \"旧前缀=新前缀\"")
    for _, dest in plan[:15]:
        print(f"  {'覆盖' if dest.exists() else '新增'}  {dest}")
    if len(plan) > 15:
        print(f"  … 其余 {len(plan) - 15} 项")

    if args.dry_run or not args.force:
        print("\n预览完毕。确认后加 --force 执行。")
        return 0

    print(f"\n已恢复 {apply_plan(plan, wb, force=True)} 个文件")
    print("下一步：重启客户端；密钥字段需手工补回；连接器需重新授权。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
