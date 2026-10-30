#!/usr/bin/env python3
"""首次使用：问清"备份存哪"，把仓库和本地目录准备好。

设计假设：调用方可能是一个 AI Agent，也可能是一个刚装好工具的人。
所以两条路都通——
* 终端交互：直接问你远程地址和本地目录；
* 参数驱动：Agent 在对话里问完用户，用 --remote/--local 传进来，不阻塞等待输入。

用法
----
    # 只在本机建仓库，不推远程
    python scripts/init.py --local ~/WorkBuddyBackup

    # 已有远程仓库
    python scripts/init.py --remote https://github.com/me/wb-config-backup.git --local ~/WorkBuddyBackup

    # 远程还没有，让脚本用 GitHub API 建一个私有库（需要 PAT）
    python scripts/init.py --remote https://github.com/me/wb-config-backup.git \
        --local ~/WorkBuddyBackup --create-remote --token ghp_xxx
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import common

DEFAULT_LOCAL = Path.home() / "WorkBuddyBackup"
GITIGNORE = """# 备份仓库里只存文本快照
*.db
*.db-wal
*.db-shm
*.log
*.tmp
*.ndjson
Thumbs.db
.DS_Store
"""


def ask(question: str, default: str = "") -> str:
    hint = f"（默认 {default}）" if default else ""
    try:
        answer = input(f"{question}{hint}：").strip()
    except EOFError:
        return default
    return answer or default


def repo_name_from_url(url: str) -> str:
    cleaned = url.rstrip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    return cleaned.split("/")[-1] or "config-backup"


def create_github_repo(url: str, token: str, private: bool = True) -> bool:
    """远程还不存在时新建。用官方 REST API，避免依赖 gh CLI。"""
    name = repo_name_from_url(url)
    body = json.dumps({
        "name": name,
        "private": private,
        "description": "WorkBuddy / CodeBuddy 私有配置备份（自动生成）",
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://api.github.com/user/repos",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "wb-config-backup-init",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            print(f"已创建 GitHub 仓库：{json.loads(resp.read())['full_name']}")
            return True
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        if exc.code == 422:
            print(f"仓库已存在或名称不可用（422），直接使用：{detail}")
            return True
        print(f"创建失败 HTTP {exc.code}：{detail}")
        return False
    except urllib.error.URLError as exc:
        print(f"网络不可达：{exc.reason}")
        return False


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def init_git(repo: Path, branch: str, remote: str) -> bool:
    """本地为空、远程已有内容时优先 clone——换电脑时走的就是这条。"""
    if remote and not (repo / ".git").exists() and (
        not repo.exists() or not any(repo.iterdir())
    ):
        repo.parent.mkdir(parents=True, exist_ok=True)
        result = run(["git", "clone", remote, str(repo)])
        if result.returncode == 0:
            print(f"已克隆远程仓库到：{repo}")
            return True
        print("克隆失败，改为本地初始化：", (result.stderr or result.stdout).strip()[:200])

    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        result = run(["git", "init", "-b", branch], cwd=repo)
        if result.returncode != 0:
            print("git init 失败：", (result.stderr or result.stdout)[:300])
            return False
        print(f"已初始化本地仓库：{repo}")

    (repo / ".gitignore").write_text(GITIGNORE, encoding="utf-8")

    if remote:
        remotes = run(["git", "remote"], cwd=repo).stdout.split()
        if "origin" in remotes:
            run(["git", "remote", "set-url", "origin", remote], cwd=repo)
        else:
            run(["git", "remote", "add", "origin", remote], cwd=repo)

    if not run(["git", "status", "--porcelain"], cwd=repo).stdout.strip():
        if not run(["git", "rev-parse", "HEAD"], cwd=repo).returncode == 0:
            (repo / "README.md").write_text(
                "# 配置备份仓库\n\n由 wb-config-backup skill 自动维护，请勿手工编辑。\n",
                encoding="utf-8",
            )
        run(["git", "add", "-A"], cwd=repo)
        run(["git", "commit", "-m", "chore: 初始化备份仓库"], cwd=repo)
        print("已创建初始提交")
    return True


def main(argv: list[str] | None = None) -> int:
    common.main_guard()
    parser = argparse.ArgumentParser(description="初始化配置备份仓库")
    parser.add_argument("--remote", help="远程仓库地址（https 或 ssh），留空则只做本地备份")
    parser.add_argument("--local", help="本地备份仓库目录")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--project-root", action="append", default=[],
                        help="扫描项目规则的根目录，可重复")
    parser.add_argument("--wb-dir", help="WorkBuddy 配置目录，默认自动探测")
    parser.add_argument("--cb-dir", help="CodeBuddy 配置目录，默认自动探测")
    parser.add_argument("--create-remote", action="store_true",
                        help="远程不存在时用 GitHub API 新建私有库")
    parser.add_argument("--token", default="", help="GitHub PAT，默认读 GITHUB_TOKEN")
    parser.add_argument("--no-interactive", action="store_true",
                        help="缺参数直接报错，不等待输入（供 Agent 调用）")
    args = parser.parse_args(argv)

    if not shutil.which("git"):
        print("未找到 git，请先安装 Git for Windows / git。")
        return 1

    interactive = not args.no_interactive and sys.stdin.isatty()

    remote = args.remote or ""
    local = args.local or ""

    if not local:
        if interactive:
            local = ask("本地备份仓库放在哪", str(DEFAULT_LOCAL))
        else:
            local = str(DEFAULT_LOCAL)

    if not remote and interactive and not args.no_interactive:
        remote = ask("远程 Git 地址（直接回车表示只备份到本地）", "")

    repo = Path(local).expanduser()
    token = args.token or os.environ.get("GITHUB_TOKEN", "")

    if args.create_remote:
        if not remote:
            print("--create-remote 需要同时提供 --remote")
            return 2
        if not create_github_repo(remote, token, private=True):
            print("可先到 GitHub 手动建库，再重跑一次（去掉 --create-remote）。")
            return 1

    if not init_git(repo, args.branch, remote):
        return 1

    cfg = common.load_config()
    cfg.update({
        "repo": str(repo),
        "remote": remote,
        "branch": args.branch,
        "project_roots": args.project_root or cfg.get("project_roots", []),
        "wb_dir": args.wb_dir or str(common.wb_root(cfg)),
        "cb_dir": args.cb_dir or str(common.cb_root(cfg)),
    })
    common.save_config(cfg)

    print(f"\n配置已写入：{common.CONFIG_PATH}")
    print(f"  本地仓库：{repo}")
    print(f"  远程地址：{remote or '（未设置，仅本地备份）'}")
    if not common.wb_root(cfg).exists():
        print("  提示：WorkBuddy 目录还不存在，先启动一次客户端再备份")
    print("\n下一步：python scripts/backup.py")
    if remote:
        print("首次推送会要求输入 GitHub 用户名和 PAT（不是账号密码）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
