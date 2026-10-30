#!/usr/bin/env python3
"""一键把远程快照拉到本机并恢复（换电脑 / 第二台设备）。

前提：init.py 已经写好 config.json（本地仓库路径 + 远程地址）。

用法
----
    python scripts/bootstrap.py --dry-run
    python scripts/bootstrap.py --force
    python scripts/bootstrap.py --force --map "D:/projects=E:/AIDev"
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import common
import restore


def pull(repo: Path, branch: str) -> None:
    if not (repo / ".git").exists():
        print(f"本地还没有仓库：{repo}\n先执行 python scripts/init.py --remote <地址> --local <目录>")
        return
    result = subprocess.run(
        ["git", "-C", str(repo), "pull", "--ff-only", "origin", branch],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        # 拉不到不算致命：本地快照仍可恢复
        print("拉取失败，使用本地已有快照：", (result.stderr or result.stdout).strip()[:300])
    else:
        tail = (result.stdout.strip().splitlines() or ["已是最新"])[-1]
        print(f"已同步远程：{tail}")


def redacted_files(repo: Path) -> list[str]:
    hits: list[str] = []
    for path in repo.rglob("*.json"):
        if ".git" in path.parts:
            continue
        try:
            if common.REDACTED in path.read_text(encoding="utf-8"):
                hits.append(path.relative_to(repo).as_posix())
        except (OSError, UnicodeDecodeError):
            continue
    return sorted(hits)


def main(argv: list[str] | None = None) -> int:
    common.main_guard()
    parser = argparse.ArgumentParser(description="拉取快照并恢复")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-pull", action="store_true")
    parser.add_argument("--wb-dir")
    parser.add_argument("--cb-dir")
    parser.add_argument("--personal-dir")
    parser.add_argument("--map", action="append", default=[], metavar="OLD=NEW")
    args = parser.parse_args(argv)

    cfg = common.require_config()
    if cfg is None:
        return 1
    repo = common.repo_path(cfg)
    wb = Path(args.wb_dir).expanduser() if args.wb_dir else common.wb_root(cfg)
    cb = Path(args.cb_dir).expanduser() if args.cb_dir else common.cb_root(cfg)

    print("== 环境检查 ==")
    print(f"仓库：{repo}{'' if repo.exists() else '（不存在）'}")
    print(f"WorkBuddy 目录：{wb}{'' if wb.exists() else '（将创建）'}")
    print(f"CodeBuddy 目录：{cb}{'' if cb.exists() else '（将创建）'}")

    if not args.dry_run and not args.no_pull:
        pull(repo, cfg["branch"])

    if not (repo / "MANIFEST.json").exists():
        print("仓库里没有 MANIFEST.json，无法恢复。")
        return 1

    plan, skipped, personal = restore.build_plan(
        repo, wb, cb, restore.parse_maps(args.map), args.personal_dir
    )
    print("\n== 恢复计划 ==")
    print(f"个人目录：{personal if personal else '未找到（跳过 identity/personal）'}")
    print(f"待恢复 {len(plan)} 个文件，跳过 {len(skipped)} 个")
    if any(r.startswith("projects/") for r in skipped):
        print("  ! 部分项目文件路径无法定位，跨机器请加 --map \"旧前缀=新前缀\"")
    for _, dest in plan[:15]:
        print(f"  {'覆盖' if dest.exists() else '新增'}  {dest}")
    if len(plan) > 15:
        print(f"  … 其余 {len(plan) - 15} 项")

    if args.dry_run or not args.force:
        print("\n预览完毕。确认后加 --force 执行。")
        return 0

    print(f"\n已恢复 {restore.apply_plan(plan, wb, force=True)} 个文件")
    print("\n== 还需要手工做 ==")
    print("1. 重启 WorkBuddy / CodeBuddy（配置只在启动时读一次）")
    print("2. 登录同一账号；连接器需重新授权")
    secrets = redacted_files(repo)
    if secrets:
        print("3. 补回密钥（以下文件已脱敏）：")
        for rel in secrets:
            print(f"   - {rel}")
    else:
        print("3. 无脱敏字段需补")
    return 0


if __name__ == "__main__":
    sys.exit(main())
