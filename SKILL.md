---
name: cc-port
description: Publish, register, scan, compare, upload and download AI agent resources (skills, MCP servers, rules, prompts and plugins) across Cursor, Claude Code and other AI coding platforms through CC Port. Use when the user wants to publish a local resource, register a third-party resource, scan local AI tools, compare local and remote states, batch upload resources, download resources to selected tools, check for updates, link skills to a project, or search available skills. Triggers include phrases like "publish/upload/share this skill", "scan local resources", "download my skills to Cursor", "sync skills", "add MCP server", "register MCP", "link skills", "search skills", "cc-port", "发布 skill", "扫描本地资源", "下载到 Cursor", "同步技能", "搜索技能", "链接技能", "添加 MCP 服务器".
metadata:
  version: "0.5.2"
---

# CC Port (cc-port)

CC Port 管理 AI 编程助手的五类资源 -- skill、MCP 服务器配置、rule、prompt、plugin -- 跨多个 AI 编程平台（Cursor、Claude Code、Windsurf、Codex 等）。通过 MCP 工具（或 `cc-port` CLI）完成发布、登记、安装、同步、搜索、项目级链接等操作。

当前版本为 `0.5.2`，源码仓库为 <https://github.com/Ling-ye/cc-port>。

## 快速上手（三步）

```
1. cc-port init                              # 生成 ~/.config/cc-port/config.toml
2. 编辑 config.toml 填 owner（或设 $env:CC_PORT_GITHUB_TOKEN 环境变量）
3. cc-port publish <skill目录> --private -y  # 发布到 GitHub 私有仓库
```

## 配置

所有配置从 `~/.config/cc-port/config.toml` 读取（`cc-port init` 生成模板）。

| 字段 | 说明 |
|------|------|
| `[github].token` | GitHub PAT，也可用 `CC_PORT_GITHUB_TOKEN` 环境变量代替（优先级更高） |
| `[github].owner` | 发布仓库时的 GitHub 用户名或组织名 |
| `[github].repo_prefix` | 新建仓库的名称前缀，默认 `cc-port-` |
| `[github].default_private` | 默认仓库可见性 |
| `[platforms.cursor]` | Cursor 平台的 skills 目录、mcp.json 路径 |
| `[platforms.claude-code]` | Claude Code 平台配置（可选启用） |
| `[platforms.<任意名>]` | 自定义平台，只需填 skills_dir / mcp_json / rules_dir |

## 意图 -> 工具映射

| 用户意图 | MCP 工具 |
|---------|---------|
| "发布 / 上传这个 skill 到 GitHub" | `publish_local_skill` |
| "把仓库改成公开/私有" | `set_skill_visibility` |
| "登记 / 收藏这个 skill 仓库" | `add_external_skill` (kind="skill") |
| "添加一个 MCP 服务器" | `add_mcp_server` 或 `add_external_skill` (kind="mcp") |
| "把远端资源下载到本机工具" | CLI `cc-port asset download --all --platform <tool>` |
| "看哪些有更新" | `skill_status` |
| "更新某个资源" | `update_skill` |
| "删除 / 取消注册某个资源" | `remove_skill` |
| "列出已注册的资源" | `list_items` 或 `list_skills` |
| "查看平台配置" | `list_platforms` |
| "检查仓库可达性" | `check_items` |

不确定时，先问一个澄清问题再操作。

## 前置检查

操作 GitHub 前先确认：

1. `cc-port` 已安装（`pip install -e .` 在 CC Port 仓库目录下）
2. `git` 在 PATH 中
3. GitHub PAT 已配置（二选一）：
   - 环境变量 `CC_PORT_GITHUB_TOKEN`（推荐）
   - config.toml 中的 `[github].token`

token 缺失时不要静默失败 -- 指引用户设环境变量或编辑 config.toml。

运行 `cc-port doctor` 可一次性检查上述所有项。

## 资源类型

- **skill** -- 包含 `SKILL.md` 的 Agent 技能目录，安装到各平台 skills 目录
- **mcp** -- MCP 服务器配置（command/args/env），注入到各平台 mcp.json
- **rule** -- 编码规则和约定文件，安装到各平台 rules 目录

## 元数据字段

发布和登记时可附加元数据，便于搜索和管理：

- `--tag python --tag testing` -- 标签（可重复，用于搜索过滤）
- `--category software-dev` -- 分类
- `--platform cursor` -- 资源安装平台白名单，可重复；省略表示所有已启用平台
- `--version 1.0.0` -- 语义化版本
- `--author Lingye` -- 作者
- `--license MIT` -- 许可证

## 典型工作流

### 发布本地 skill

1. 确认目录包含有效的 `SKILL.md`（frontmatter 含 `name` 和 `description`）
2. 确认可见性：问用户"公开还是私有？"（或使用 `--private` / `--public` 跳过询问）
3. 调用 `publish_local_skill(path=<绝对路径>, private=<bool>)`
4. 提醒用户 commit 并 push `registry.yaml`

实际示例：
```
cc-port publish D:\path\to\example-skill --private -y --tag python --category productivity
# -> Published example-skill (skill) -> https://github.com/example/cc-port-example-skill.git (private, created)
```

### 登记 MCP 服务器

```
add_mcp_server(name="github", github_url="https://github.com/...",
               command="npx", args=["-y", "@modelcontextprotocol/server-github"],
               env={"GITHUB_TOKEN": "${GITHUB_TOKEN}"})
```

### 在新电脑下载全部远端资源

1. clone CC Port 仓库并 `pip install -e .`
2. `cc-port init` 生成配置，填好 token 和 owner
3. `cc-port asset list --scan-local` 查看远端与本地并集
4. `cc-port asset download --all --platform cursor --dry-run` 预览计划
5. 确认后去掉 `--dry-run` 并加 `--yes` 执行；需要多个工具时重复 `--platform`

### 项目中使用已收集的 skill

```
cd <项目目录>
cc-port link                      # 链接所有 skill 到当前项目
cc-port link --tag python          # 只链接带 python 标签的
cc-port link --only my-skill       # 只链接指定 skill
```

执行后 AI agent 会自动发现 `.cursor/rules/cc-port-skills.md` 中索引的 skill，遇到匹配场景时主动加载。

### 搜索资源

```
cc-port search python              # 本地注册表搜索
cc-port search --tag testing       # 按标签过滤
cc-port search fastapi --remote    # 同时搜索 GitHub
```

## 在其他项目中引入 CC Port

CC Port 可以作为 Python 库、Git Submodule 或 Cursor Skill 三种方式引入到其他项目中，按需选用或组合使用。

### 方式一：pip install from Git（作为 Python 包）

无需 clone 源码，直接从 Git 安装为 Python 包：

```bash
# 安装最新版
pip install git+https://github.com/Ling-ye/cc-port.git

# 锁定到特定版本/分支/commit
pip install git+https://github.com/Ling-ye/cc-port.git@main
pip install git+https://github.com/Ling-ye/cc-port.git@v0.5.2
```

在目标项目的依赖中声明：

```
# requirements.txt
cc-port @ git+https://github.com/Ling-ye/cc-port.git@main
```

```toml
# pyproject.toml
dependencies = [
    "cc-port @ git+https://github.com/Ling-ye/cc-port.git@main",
]
```

安装后代码中可 `import cc_port`，`cc-port` 和 `cc-port-mcp` 命令行工具自动可用。

### 方式二：Git Submodule（嵌入源码）

将 CC Port 仓库作为子模块嵌入到目标项目中，代码可见、可编辑、版本锁定：

```bash
cd <你的项目>
git submodule add https://github.com/Ling-ye/cc-port.git libs/cc-port
git commit -m "add CC Port as submodule"

# 安装为可编辑包
pip install -e libs/cc-port
```

团队成员 clone 时需要加 `--recurse-submodules`：

```bash
git clone --recurse-submodules <你的项目仓库>
```

### 方式三：作为 Cursor Skill 引入

CC Port 自带 `SKILL.md`，可直接作为 Cursor Skill 被 AI Agent 自动发现和使用：

```bash
# 通过 cc-port 自身登记并链接
cc-port add https://github.com/Ling-ye/cc-port.git --tag cc-port --category tool-management
cd <你的项目>
cc-port link --only cc-port
```

也可以手动在项目的 `.cursor/skills/` 下创建 symlink 指向 CC Port 目录，Agent 会自动读取 SKILL.md。

### 推荐组合

| 需求 | 方案 | 效果 |
|------|------|------|
| 代码中调用 CC Port API | 方式一（pip install） | `import cc_port`，CLI 命令可用 |
| 需要修改 CC Port 源码 | 方式二（submodule） | 源码嵌入，可直接编辑 |
| AI Agent 自动使用 CC Port | 方式三（skill） | Agent 读取 SKILL.md 自动调用 |
| 完整集成（推荐） | 方式一 + 方式三 | 既是库又是 skill |

### 修改可见性

`set_skill_visibility(name=..., private=<bool>)`，仅限 `owned` 资源。

## 硬性规则

- **token 永远不入库。** registry.yaml 和日志只存纯 HTTPS URL。git 操作通过 GIT_ASKPASS 传递 token，不写入 .git/config。
- **资源名称**：小写字母/数字/连字符，最长 64 字符。
- **平台专用资源必须声明白名单。** 例如 Cursor hooks/subagents skill 使用 `platforms: [cursor]`，不要依赖 tags 表达安装约束。
- **真实环境文件不进入资源仓库。** `.env`、`.env.local` 等默认排除，只保留无密钥模板文件。
- **发布后不要重新 git init。** 后续修改走正常 `git commit && git push`。
- **优先用 MCP 工具。** 只在 MCP 不可用时回退到 CLI。

## CLI 命令速查

```
cc-port init [--claude-code] [-f]        # 生成配置文件
cc-port doctor                           # 检查环境
cc-port publish <path> [--private/--public --kind --mcp-config --tag --category --platform --version --author --license -y]
cc-port set-visibility <name> {public|private}
cc-port add <github-url> [--subdir --ref --name --kind --mcp-config --tag --category --platform]
cc-port collect <github-tree-url> [--type --name --platform]
cc-port upload <path> [--type --name --platform]
cc-port import-local <path> [--kind --name --tag --category --platform]
cc-port export-plugin [--name]
cc-port search [query] [--tag --kind --category --remote]
cc-port link [--project --only --tag --kind]
cc-port unlink [--project]
cc-port sync [--only NAME --kind TYPE --platform NAME]
cc-port status [--kind TYPE]
cc-port check [--kind --prune --uninstall]
cc-port list [--kind TYPE]
cc-port update <name>
cc-port remove <name> [--uninstall]
cc-port install-self [--force]
cc-port platforms
```
