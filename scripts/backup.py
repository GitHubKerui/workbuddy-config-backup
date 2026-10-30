#!/usr/bin/env python3
"""把 WorkBuddy / CodeBuddy 的私有配置同步进备份仓库并推送。

用法
----
    python scripts/backup.py              # 备份 + 提交 + 推送
    python scripts/backup.py --dry-run    # 只看变化
    python scripts/backup.py --no-push    # 只提交
"""

from __future__ import annotations

import argparse
import sys

import common


def main(argv: list[str] | None = None) -> int:
    common.main_guard()
    parser = argparse.ArgumentParser(description="备份 WorkBuddy / CodeBuddy 私有配置")
    parser.add_argument("--dry-run", action="store_true", help="只看变化，不写文件不提交")
    parser.add_argument("--no-push", action="store_true", help="只提交到本地仓库")
    args = parser.parse_args(argv)

    cfg = common.require_config()
    if cfg is None:
        return 1

    repo = common.repo_path(cfg)
    if not args.dry_run:
        common.ensure_repo(repo, cfg)

    plan = common.collect(cfg)
    if not plan:
        print("未找到可备份内容，检查 config.json 里的 wb_dir / cb_dir")
        return 1

    added, updated, removed = common.sync(repo, plan, cfg, args.dry_run)
    print(f"扫描 {len(plan)} 个文件 → 新增 {len(added)}，更新 {len(updated)}，删除 {len(removed)}")

    if args.dry_run:
        for tag, items in (("+", added), ("~", updated), ("-", removed)):
            for rel in items[:30]:
                print(f"  {tag} {rel}")
        return 0

    common.commit_and_push(repo, cfg, added, updated, removed, push=cfg["auto_push"] and not args.no_push)
    return 0


if __name__ == "__main__":
    sys.exit(main())
