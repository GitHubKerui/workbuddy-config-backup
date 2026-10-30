#!/usr/bin/env python3
"""公共逻辑：配置读写、路径过滤、脱敏、Git 操作。

为什么单独抽一个模块：备份、恢复、初始化、每日检查四个脚本都要用同一套
过滤与脱敏规则，复制四份迟早会漂移，泄漏往往就发生在改了三份忘了一份的时候。
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("WB_BACKUP_CONFIG") or SKILL_DIR / "config.json")
HOME = Path.home()

DEFAULTS: dict = {
    "repo": "",              # 本地备份仓库绝对路径
    "remote": "",            # 远程地址（https 或 ssh）
    "branch": "main",
    "auto_push": True,
    "projects": [],          # 显式指定的项目根目录
    "project_roots": [],     # 扫描这些目录下带规则文件的项目
    "max_file_bytes": 2_000_000,
    "app_config_keep": ["locale", "personalization", "disableAgentTeams"],
    "extra_exclude_globs": [],
    "wb_dir": "",            # 留空则自动探测
    "cb_dir": "",
}

# 目录级排除：缓存、运行时、会话、密钥库
EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    "cache", "caches", "logs", "log", "traces", "blobs", "sessions", "session",
    "security", "clipboard-images", "shell-snapshots", "audit-log",
    "pending-telemetry", "local_storage", "media-index", "connector-keys",
    "file-history", "changes-detail", "changes-index", "artifact-index",
    "unified-asset", "editor-sdk-sandbox-ws", "wbipc", "app",
}

# 文件级排除：数据库、日志、密钥、二进制、临时态
EXCLUDE_FILE_GLOBS = (
    "*.db", "*.db-wal", "*.db-shm", "*.sqlite", "*.sqlite3", "*.log",
    "*.ndjson", "*.pem", "*.key", "*.p12", "*.pfx", "*.zip", "*.png",
    "*.jpg", "*.jpeg", "*.webp", "*.gif", "*.mp4", "*.bak", "*.tmp",
    "keyblob*", "device-id*", ".env", ".env.*", "*.lock",
    ".skill-list-cache.json", "mcp-tool-list.json", "usage-log.json",
    "session_fragment_repair_done*", "*_memory.md.bak",
    # 纯运行时标记，换机无意义，留着只会制造噪声提交
    "workspace-state.json", "user-memery-state.json",
    ".disable_to_model_invocation_migration.json",
    ".model_invocation_to_override_migration.json",
)

SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|apikey|secret|token|password|passwd|credential|"
    r"authorization|cookie|private[_-]?key|access[_-]?key|session[_-]?key)",
    re.IGNORECASE,
)
SECRET_VALUE_RES = (
    re.compile(r"^ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"^github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"^sk-[A-Za-z0-9\-_]{12,}"),
    re.compile(r"^ark-[A-Za-z0-9\-]{12,}"),
    re.compile(r"^AKIA[A-Z0-9]{12,}"),
    re.compile(r"^xox[baprs]-"),
    re.compile(r"^eyJ[A-Za-z0-9_\-]{10,}\."),
)
REDACTED = "***REDACTED***"

PROJECT_RULE_FILES = (
    "CODEBUDDY.md", "CLAUDE.md", "AGENTS.md",
    ".cursorrules", ".windsurfrules", "CODEBUDDY.local.md",
)
PROJECT_RULE_DIRS = (".workbuddy", ".codebuddy", ".cursor", ".github/copilot")
UID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def require_config() -> dict | None:
    """没配置就明确告诉调用方去初始化，不要用猜的默认值去写盘。"""
    cfg = load_config()
    if not cfg.get("repo"):
        print("尚未初始化。请先执行：python scripts/init.py")
        print("或让 WorkBuddy 帮你问用户：仓库放哪、远程地址是什么。")
        return None
    return cfg


def wb_root(cfg: dict) -> Path:
    return Path(cfg["wb_dir"]).expanduser() if cfg.get("wb_dir") else HOME / ".workbuddy"


def cb_root(cfg: dict) -> Path:
    return Path(cfg["cb_dir"]).expanduser() if cfg.get("cb_dir") else HOME / ".codebuddy"


def repo_path(cfg: dict) -> Path:
    return Path(cfg["repo"]).expanduser()


# --------------------------------------------------------------------------
# 过滤与脱敏
# --------------------------------------------------------------------------
def _excluded_name(name: str, extra: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in EXCLUDE_FILE_GLOBS + extra)


def iter_tree(root: Path, extra_globs: tuple[str, ...], max_bytes: int):
    """遍历目录，产出可以安全备份的文件。"""
    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in EXCLUDE_DIRS]
        for name in filenames:
            if _excluded_name(name, extra_globs):
                continue
            path = Path(dirpath) / name
            try:
                if path.stat().st_size > max_bytes:
                    continue
            except OSError:
                continue
            yield path


def redact(value, key: str = ""):
    """递归脱敏。按键名 + 值模式双判：有的密钥藏在 env 里没有显眼键名。"""
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, key) for v in value]
    if isinstance(value, str):
        if key and SECRET_KEY_RE.search(key):
            return REDACTED
        if len(value) >= 16 and any(p.match(value) for p in SECRET_VALUE_RES):
            return REDACTED
    return value


def render(src: Path, transform: str | None, cfg: dict) -> bytes | None:
    try:
        if transform in ("redact_json", "keep_keys_json"):
            data = json.loads(src.read_text(encoding="utf-8"))
            if transform == "keep_keys_json":
                keep = cfg["app_config_keep"]
                data = {k: v for k, v in data.items() if k in keep}
            data = redact(data)
            return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        return src.read_bytes()
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"  ! 跳过 {src}（{exc.__class__.__name__}）")
        return None


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def slugify(path: Path) -> str:
    text = str(path.resolve()).replace(":", "").replace("\\", "/").strip("/")
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text)


def discover_projects(cfg: dict) -> list[Path]:
    """显式列表 + 根目录扫描（只认带规则痕迹的一级子目录）。"""
    found: list[Path] = []
    for raw in cfg.get("projects", []):
        p = Path(raw).expanduser()
        if p.is_dir():
            found.append(p)
    for raw in cfg.get("project_roots", []):
        root = Path(raw).expanduser()
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.lower() in EXCLUDE_DIRS:
                continue
            if any((child / n).exists() for n in PROJECT_RULE_FILES) or any(
                (child / d).is_dir() for d in (".workbuddy", ".codebuddy")
            ):
                found.append(child)
    seen: set[str] = set()
    unique: list[Path] = []
    for p in found:
        key = str(p.resolve()).lower()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


# --------------------------------------------------------------------------
# 采集
# --------------------------------------------------------------------------
def collect(cfg: dict) -> dict[str, tuple[Path | None, str | None, bytes | None]]:
    """返回 {仓库内相对路径: (源文件, 渲染方式, 直接给定内容)}"""
    extra = tuple(cfg.get("extra_exclude_globs", []))
    max_bytes = int(cfg["max_file_bytes"])
    wb, cb = wb_root(cfg), cb_root(cfg)
    plan: dict[str, tuple[Path | None, str | None, bytes | None]] = {}

    def add_tree(root: Path, prefix: str):
        for path in iter_tree(root, extra, max_bytes):
            rel = path.relative_to(root).as_posix()
            plan[f"{prefix}/{rel}"] = (path, None, None)

    def add_file(src: Path, rel: str, transform: str | None = None):
        if src.is_file():
            plan[rel] = (src, transform, None)

    # ---- WorkBuddy ----
    add_tree(wb / "skills", "workbuddy/skills")
    add_tree(wb / "experts", "workbuddy/experts")
    for name in ("SOUL.md", "IDENTITY.md", "USER.md", "MEMORY.md"):
        add_file(wb / name, f"workbuddy/identity/account/{name}")
    for pdir in sorted(wb.glob("user-*-personal")):
        for path in iter_tree(pdir, extra, max_bytes):
            rel = f"workbuddy/identity/personal/{path.relative_to(pdir).as_posix()}"
            plan[rel] = (path, None, None)
    add_tree(wb / "memory", "workbuddy/memory")
    add_file(wb / "app" / "app-config.json", "workbuddy/config/app-config.json", "keep_keys_json")
    add_file(wb / "settings.json", "workbuddy/config/settings.json", "redact_json")
    add_file(wb / "mcp.json", "workbuddy/config/mcp.json", "redact_json")

    # ---- CodeBuddy ----
    add_tree(cb / "skills", "codebuddy/skills")
    add_tree(cb / "agents", "codebuddy/agents")
    add_tree(cb / "memery", "codebuddy/memory")
    add_file(cb / "settings.json", "codebuddy/config/settings.json", "redact_json")
    add_file(cb / "models.json", "codebuddy/config/models.json", "redact_json")
    add_file(cb / "mcp.json", "codebuddy/config/mcp.json", "redact_json")

    # ---- 项目规则 ----
    index: dict[str, str] = {}
    for proj in discover_projects(cfg):
        slug = slugify(proj)
        index[slug] = str(proj.resolve())
        for name in PROJECT_RULE_FILES:
            add_file(proj / name, f"projects/{slug}/{name}")
        for dname in PROJECT_RULE_DIRS:
            add_tree(proj / dname, f"projects/{slug}/{dname}")
    # slug 是有损的目录名，靠这份索引才能在恢复时还原真实路径
    plan["projects/index.json"] = (
        None, None,
        (json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return plan


def load_manifest(repo: Path) -> dict:
    path = repo / "MANIFEST.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"version": 1, "files": {}}


def prune_empty_dirs(start: Path, stop: Path) -> None:
    current = start
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def sync(repo: Path, plan: dict, cfg: dict, dry_run: bool) -> tuple[list[str], list[str], list[str]]:
    """写入有变化的文件，删除失联文件。返回 (新增, 修改, 删除)。"""
    manifest = load_manifest(repo)
    old = manifest.get("files", {})
    new_hashes: dict[str, str] = {}
    added, updated, removed = [], [], []

    for rel in sorted(plan):
        src, transform, preset = plan[rel]
        data = preset if preset is not None else render(src, transform, cfg)
        if data is None:
            continue
        digest = sha(data)
        new_hashes[rel] = digest
        dest = repo / rel
        if old.get(rel) == digest and dest.exists():
            continue
        (added if rel not in old else updated).append(rel)
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)

    for rel in sorted(set(old) - set(new_hashes)):
        removed.append(rel)
        if not dry_run:
            target = repo / rel
            if target.exists():
                target.unlink()
            prune_empty_dirs(target.parent, repo)

    if (added or updated or removed) and not dry_run:
        (repo / "MANIFEST.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "files": new_hashes,
                },
                ensure_ascii=False, indent=2, sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )
    return added, updated, removed


# --------------------------------------------------------------------------
# Git
# --------------------------------------------------------------------------
def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=check,
    )


def ensure_repo(repo: Path, cfg: dict) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        git(repo, "init", "-b", cfg["branch"])
        print(f"已初始化仓库：{repo}")
    if cfg.get("remote"):
        remotes = git(repo, "remote", check=False).stdout.split()
        if "origin" not in remotes:
            git(repo, "remote", "add", "origin", cfg["remote"])


def commit_and_push(repo: Path, cfg: dict, added, updated, removed, push: bool) -> bool:
    git(repo, "add", "-A")
    if not git(repo, "status", "--porcelain").stdout.strip():
        print("无变化，跳过提交")
        return False
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    subject = f"backup({now}): 新增{len(added)} 更新{len(updated)} 删除{len(removed)}"
    lines = [subject, ""]
    for tag, items in (("新增", added), ("更新", updated), ("删除", removed)):
        for rel in items[:20]:
            lines.append(f"{tag}: {rel}")
        if len(items) > 20:
            lines.append(f"{tag}: 其余 {len(items) - 20} 项略")
    git(repo, "commit", "-m", subject, "-m", "\n".join(lines))
    print(f"已提交：{subject}")

    if not push:
        print("已跳过推送")
        return True
    result = git(repo, "push", "-u", "origin", cfg["branch"], check=False)
    if result.returncode != 0:
        print("推送失败：", (result.stderr or result.stdout).strip()[:500])
        print(f"→ 配置好凭据后重试：git -C {repo} push -u origin {cfg['branch']}")
    else:
        print(f"已推送 origin/{cfg['branch']}")
    return True


def main_guard() -> None:
    """Windows 控制台默认 GBK，直接 print 中文以外的符号容易崩，统一改成 utf-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
