---
name: config-backup
description: >
  把 WorkBuddy / CodeBuddy 的自定义 Skill、自建 Agent、全局规则、项目规则、身份与记忆文件
  备份到用户自己的 Git 仓库（私有），并在换电脑时一键恢复。自动排除运行时数据库、日志、缓存，
  密钥字段自动脱敏。首次使用时主动向用户询问远程 Git 地址与本地目录。
  当用户说"备份我的配置""备份 skill""同步 agent 和规则""换电脑怎么恢复""迁移配置"
  "backup my config""restore config" 时使用。
argument-hint: "[init|run|check|restore]"
license: MIT
---

# 配置备份 / 迁移

把"官方同步不了、丢了要重做"的东西存进用户自己的 Git 仓库：
自定义 skill、自建 agent、全局宪法规则、项目规则、身份与记忆文件。

## 首次使用：先问，再动手

判断是否已初始化：`~/.workbuddy/skills/config-backup/config.json` 是否存在。

**不存在时不要自己编路径，必须问用户两件事：**

1. **远程 Git 地址**是什么？（GitHub / Gitee / 自建，https 或 ssh 都行）
2. **本地备份目录**放哪？（默认建议 `~/WorkBuddyBackup`，不要放在 WorkBuddy 配置目录里）

然后按情况执行：

```bash
# 远程仓库已经存在
python ~/.workbuddy/skills/config-backup/scripts/init.py \
  --remote https://github.com/<用户名>/wb-config-backup.git \
  --local ~/WorkBuddyBackup

# 远程还没有，让脚本调 GitHub API 建一个 private 库（需要用户提供 PAT）
python ~/.workbuddy/skills/config-backup/scripts/init.py \
  --remote https://github.com/<用户名>/wb-config-backup.git \
  --local ~/WorkBuddyBackup --create-remote --token <PAT>

# 用户只想存本地，不推远程
python ~/.workbuddy/skills/config-backup/scripts/init.py --local ~/WorkBuddyBackup
```

问的时候一次问完，别挤牙膏。用户答不上来就给默认值：
远程地址可以用 GitHub 私有库 `wb-config-backup`，本地目录用 `~/WorkBuddyBackup`。

初始化完成后立刻跑一次 `backup.py` 验证链路通不通。

## 日常

| 目的 | 命令 |
|---|---|
| 备份 + 提交 + 推送 | `python ~/.workbuddy/skills/config-backup/scripts/backup.py` |
| 只看变化 | `python .../backup.py --dry-run` |
| 只提交不推送 | `python .../backup.py --no-push` |
| 每日一次（当天去重） | `python .../daily_check.py` |
| 预览恢复 | `python .../restore.py --dry-run` |
| 执行恢复 | `python .../restore.py --force` |
| 换机一键恢复 | `python .../bootstrap.py --force` |

执行后向用户汇报「新增/更新/删除」数量与推送结果，不要贴完整 diff。

## 换电脑 / 第二台设备

```bash
# 1. 在新机器安装本 skill（公开仓库，直接 clone 到 skills 目录）
git clone <本 skill 的仓库地址> ~/.workbuddy/skills/config-backup

# 2. 问用户要备份仓库的地址和想放的本地目录，然后初始化（远程已有内容会自动 clone）
python ~/.workbuddy/skills/config-backup/scripts/init.py \
  --remote <用户的备份仓库地址> --local <本地目录>

# 3. 预演，确认个人目录与项目路径都对
python ~/.workbuddy/skills/config-backup/scripts/bootstrap.py --dry-run

# 4. 执行
python ~/.workbuddy/skills/config-backup/scripts/bootstrap.py --force
```

项目路径和旧机器不同时加映射：`--map "D:/projects=E:/AIDev"`。

`bootstrap.py` 会处理三个换机陷阱：

| 陷阱 | 处理 |
|---|---|
| 配置目录不存在 | 自动创建；`user-<uid>-personal` 目录名带 uid，没登录过就从仓库里的 `<uid>_memory.md` 反推 |
| 项目路径写死旧机器 | `--map` 重映射；无盘符的非法路径直接跳过，不乱写 |
| 仓库没拉下来 | `init.py` 会自动 clone；`bootstrap.py` 会先 `git pull --ff-only` |

恢复后必须提醒用户：

1. 重启 WorkBuddy / CodeBuddy（配置只在启动时读一次）
2. 登录同一账号，连接器重新授权
3. 补回被脱敏的密钥（脚本会列出文件清单）
4. 市场插件按 `settings.json` 的 `enabledPlugins` 自动重装

## 备份范围

| 来源 | 仓库内位置 |
|---|---|
| `~/.workbuddy/skills/` | `workbuddy/skills/` |
| `~/.workbuddy/experts/` | `workbuddy/experts/` |
| `~/.workbuddy/{SOUL,IDENTITY,USER,MEMORY}.md` | `workbuddy/identity/account/` |
| `~/.workbuddy/user-*-personal/` | `workbuddy/identity/personal/` |
| `~/.workbuddy/memory/` | `workbuddy/memory/` |
| `~/.workbuddy/app/app-config.json`（仅 `personalization` 等字段） | `workbuddy/config/app-config.json` |
| `~/.workbuddy/{settings,mcp}.json` | `workbuddy/config/` |
| `~/.codebuddy/skills`、`agents`、`memery` | `codebuddy/skills|agents|memory/` |
| `~/.codebuddy/{settings,models,mcp}.json` | `codebuddy/config/` |
| 项目规则 `CODEBUDDY.md` / `CLAUDE.md` / `AGENTS.md` / `.workbuddy/` / `.codebuddy/` | `projects/<slug>/` |

扫描范围由 `config.json` 的 `project_roots` 决定。用户抱怨"某个项目没备份"时，
把它加到 `projects` 数组或加 `--project-root`。

## 安全边界（不可协商）

- **绝不**备份 `workbuddy.db`、`*.db-wal`、`sessions/`、`*.ndjson`、`logs/`、`cache/`、`blobs/`、`keyblob`、`device-id`、`connector-keys/`、`.env`
- 写入前脱敏：键名命中 `apiKey/token/secret/password/...` 或值命中 `ghp_/sk-/AKIA/eyJ...` → `***REDACTED***`
- 备份仓库必须是 **private**。用户要求改 public 时明确劝阻。
- 不要 `git push --force`，不要改写历史。

## 排错

| 现象 | 处理 |
|---|---|
| 提示"尚未初始化" | 跑 `init.py`，见上文首次使用 |
| 推送失败 | 凭据问题：`git -C <repo> push -u origin main` 手动输一次用户名 + PAT，让系统凭据管理器记住 |
| 恢复后规则没生效 | 没重启客户端 |
| 备份里项目不全 | `project_roots` 没覆盖到，或项目没有 `CODEBUDDY.md` 等规则痕迹 |
