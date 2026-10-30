# WorkBuddy / CodeBuddy 配置备份 Skill

把你的 **自定义 Skill、自建 Agent、全局规则、项目规则、身份与记忆文件** 备份到自己的 Git 私有仓库，
换电脑时一条命令搬过去。

WorkBuddy 官方只同步账号、订阅和市场插件；`~/.workbuddy` 和 `~/.codebuddy` 里的私人配置**只存在本机**，
换机就没了。这个 skill 补上这一段，并且刻意**不碰**运行时数据与密钥。

## 它备份什么

| 内容 | 说明 |
|---|---|
| 自定义 Skill | `~/.workbuddy/skills`、`~/.codebuddy/skills` |
| 自建 Agent | `~/.codebuddy/agents` |
| 全局规则 | `app-config.json` 里的 `personalization`（即"宪法"提示词） |
| 项目规则 | 项目目录的 `CODEBUDDY.md` / `CLAUDE.md` / `AGENTS.md` / `.workbuddy` / `.codebuddy` |
| 身份与记忆 | `SOUL.md`、`IDENTITY.md`、`USER.md`、`MEMORY.md`、记忆文件 |
| MCP 配置 | `mcp.json`（密钥已脱敏） |

## 它不备份什么

- 运行时数据库 `workbuddy.db`、`*.db-wal`、`sessions/`、`*.ndjson`
- 日志、缓存、trace、blob、剪贴板图片
- 密钥库 `keyblob`、`device-id`、`connector-keys/`、`.env`
- **密钥字段写入前统一脱敏**：`apiKey` / `token` / `secret` / `password` / `env` 中的敏感值
  替换为 `***REDACTED***`，值形如 `ghp_…`、`sk-…`、`AKIA…`、JWT 的也会被识别

入库的是**可读文本快照**，不是配置的二进制拷贝——出问题能直接看 diff 定位。

## 安装

```bash
git clone https://github.com/<你的账号>/workbuddy-config-backup.git ~/.workbuddy/skills/config-backup
```

CodeBuddy 用户想共用一份，可以再软链或复制到 `~/.codebuddy/skills/config-backup`。

## 第一次使用

直接在 WorkBuddy 里说"帮我备份配置"，Agent 会问你两件事：

1. 远程 Git 地址（还没有？可以让它用 GitHub API 帮你建一个 **private** 库）
2. 本地备份目录放哪（默认建议 `~/WorkBuddyBackup`）

也可以自己手跑：

```bash
# 远程仓库已存在
python ~/.workbuddy/skills/config-backup/scripts/init.py \
  --remote https://github.com/me/wb-config-backup.git \
  --local ~/WorkBuddyBackup

# 远程还没有，让脚本建私有库（需要 PAT，scope 含 repo）
python ~/.workbuddy/skills/config-backup/scripts/init.py \
  --remote https://github.com/me/wb-config-backup.git \
  --local ~/WorkBuddyBackup --create-remote --token ghp_xxx

# 只在本机备份
python ~/.workbuddy/skills/config-backup/scripts/init.py --local ~/WorkBuddyBackup
```

然后跑一次备份：

```bash
python ~/.workbuddy/skills/config-backup/scripts/backup.py
```

## 日常命令

| 目的 | 命令 |
|---|---|
| 备份 + 提交 + 推送 | `backup.py` |
| 只看变化 | `backup.py --dry-run` |
| 每日一次（当天去重） | `daily_check.py` |
| 预览恢复 | `restore.py --dry-run` |
| 执行恢复 | `restore.py --force` |
| 换机一键恢复 | `bootstrap.py --force` |

`scripts/` 下五个脚本的职责：

| 脚本 | 作用 |
|---|---|
| `init.py` | 问清地址、建库、建目录、写配置 |
| `backup.py` | 内容哈希判变化 → 同步进仓库 → 提交推送 |
| `restore.py` | 按清单反向还原，覆盖前自动归档 |
| `bootstrap.py` | pull 最新 + 全量恢复 + 列出待办 |
| `daily_check.py` | 当天只跑一次，供开机项和定时任务共用 |

## 自动备份

**Windows 开机自启**：把下面内容存成 `wb-config-backup.bat`，放进
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\`：

```bat
@echo off
start "" /min pythonw.exe "%USERPROFILE%\.workbuddy\skills\config-backup\scripts\daily_check.py"
```

**WorkBuddy 定时任务**：新建一个每日自动化，执行同一条 `daily_check.py`。
两条路都靠 `~/.workbuddy/backup-last-check.stamp` 当天去重，不会重复提交。

## 换电脑

```bash
git clone https://github.com/<你的账号>/workbuddy-config-backup.git ~/.workbuddy/skills/config-backup
python ~/.workbuddy/skills/config-backup/scripts/init.py --remote <备份仓库地址> --local ~/WorkBuddyBackup
python ~/.workbuddy/skills/config-backup/scripts/bootstrap.py --dry-run
python ~/.workbuddy/skills/config-backup/scripts/bootstrap.py --force
```

项目路径变了就加 `--map "D:/projects=E:/AIDev"`。

三个换机坑已经处理：

- 配置目录不存在 → 自动建
- 个人目录名带 uid、新机未登录 → 从仓库里的 `<uid>_memory.md` 反推
- 项目路径是旧机器绝对路径 → `--map` 重映射

## 安全说明

- 仓库**必须 private**。里面是你的人格、记忆、项目规则，泄漏等于公开你的工作习惯。
- 脱敏是单向的：恢复后需要手工补回密钥（换机本来也要重新授权）。
- 恢复会先把你当前文件归档到 `~/.workbuddy/restore-backup-<时间戳>/`。
- 不做 `push --force`，不改写历史。

## 许可

MIT
