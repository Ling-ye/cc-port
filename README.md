# SkillHub

> AI 编程助手的资源中央仓库 —— 一行命令把 skill、MCP 服务器、规则同步到任意一台新电脑。

SkillHub 是一个开源工具，同时提供 **CLI** 和 **MCP server** 两种入口，跨 **Cursor** 和 **Claude Code** 管理三种资源：

| 资源类型 | 说明 | 安装位置 |
|---------|------|---------|
| **Skill** | 包含 `SKILL.md` 的 Agent 技能目录 | 各平台 skills 目录 |
| **MCP Server** | MCP 服务器配置（command/args/env） | 注入各平台 mcp.json |
| **Rule** | 编码规则和约定文件 | 各平台 rules 目录 |

核心场景：

1. **发布** — 把本地目录一键发布为独立的 GitHub 仓库
2. **登记** — 把别人的 skill / MCP 配置记入清单
3. **迁移** — 换电脑时 `skillhub sync` 一条命令，所有资源自动落地

所有资源清单存在 [`registry.yaml`](registry.yaml) 中，跟着 git 一起走。

---

## 功能一览

| 能力 | 命令 / MCP 工具 | 说明 |
|------|----------------|------|
| 发布本地资源 | `skillhub publish <path> [--kind]` | 校验 → 建仓 → push → 写入清单 |
| 修改可见性 | `skillhub set-visibility <name> public\|private` | 公开或转私有 |
| 登记第三方 skill | `skillhub add <github-url>` | 记 URL + ref |
| 登记 MCP 服务器 | `skillhub add <url> --kind mcp --mcp-config '{...}'` | 记 MCP 配置 |
| 同步全部 | `skillhub sync` | 安装到所有启用平台 |
| 按平台同步 | `skillhub sync --platform cursor` | 只装到指定平台 |
| 按类型同步 | `skillhub sync --kind mcp` | 只同步 MCP 配置 |
| 更新单个 | `skillhub update <name>` | 强制同步一条 |
| 查看状态 | `skillhub status` | 对比本地/远端 commit |
| 列出清单 | `skillhub list [--kind]` | 按类型过滤 |
| 查看平台 | `skillhub platforms` | 显示平台和路径 |
| 移除 | `skillhub remove <name> [--uninstall]` | 可选同时删本地文件 |
| 环境检查 | `skillhub doctor` | 检查 git / token / 平台 |
| 安装自身 | `skillhub install-self` | 复制到所有启用平台 |

---

## 安装

前置要求：Python >= 3.10、`git` 已装好。

```bash
git clone https://github.com/<你的用户名>/SkillHub.git
cd SkillHub
pip install -e .
```

安装后有两个命令：

- `skillhub` — 终端 CLI
- `skillhub-mcp` — MCP server（Cursor / Claude Code 调用）

> Windows 如果提示找不到命令，把 pip 提示的 `Scripts` 目录加到 `PATH`，或用 `python -m skillhub.cli ...` 代替。

---

## 首次配置

### 两步搞定

```bash
# 1. 生成配置文件（默认只启用 Cursor）
skillhub init

# 同时启用 Claude Code：
skillhub init --claude-code
```

`init` 会在 `~/.config/skillhub/config.toml` 生成一个带注释的模板：

```toml
[github]
token = ""                     # 填你的 GitHub PAT，或改用环境变量
owner = ""                     # 填你的 GitHub 用户名
repo_prefix = "cursor-skill-"
default_private = false

[platforms.cursor]
enabled = true
skills_dir = "~/.cursor/skills"
mcp_json = "~/.cursor/mcp.json"
rules_dir = ""
```

```bash
# 2. 编辑 config.toml，填好 token 和 owner
#    Windows: %USERPROFILE%\.config\skillhub\config.toml
#    macOS/Linux: ~/.config/skillhub/config.toml
```

填好后运行 `skillhub doctor` 验证。

### token 也可以用环境变量

```bash
# Linux / macOS
export SKILLHUB_GITHUB_TOKEN=ghp_xxxxxxxx

# Windows PowerShell
$env:SKILLHUB_GITHUB_TOKEN = "ghp_xxxxxxxx"
```

环境变量优先于 config.toml 中的 `token` 字段。

---

## 多平台支持

config.toml 中可以配置多个平台：

```toml
[platforms.cursor]
enabled = true
skills_dir = "~/.cursor/skills"
mcp_json = "~/.cursor/mcp.json"

[platforms.claude-code]
enabled = true
skills_dir = "~/.claude/skills"
mcp_json = "~/.claude.json"
```

`skillhub sync` 安装到所有启用平台，也可以用 `--platform` 限定：

```bash
skillhub sync --platform cursor
skillhub sync --platform claude-code
```

---

## 日常使用

### 发布本地 skill

```bash
# 最短路径 — 跳过询问，用默认可见性
skillhub publish D:\dev\my-skill -y

# 明确指定
skillhub publish D:\dev\my-skill --public
skillhub publish D:\dev\my-skill --private

# 发布 MCP 服务器配置
skillhub publish D:\dev\my-mcp --kind mcp \
  --mcp-config '{"command":"node","args":["server.js"]}'
```

### 登记第三方资源

```bash
# Skill
skillhub add https://github.com/someone/awesome-skill

# MCP 服务器
skillhub add https://github.com/someone/mcp-server --kind mcp \
  --mcp-config '{"command":"npx","args":["-y","@someone/mcp-server"]}'

# 仓库子目录
skillhub add https://github.com/anthropics/skills --subdir pdf --ref main
```

### 换电脑一键迁移

```bash
git clone https://github.com/<你的用户名>/SkillHub.git
cd SkillHub
pip install -e .
skillhub init              # 生成配置模板
# 编辑 config.toml 填好 token 和 owner（或设环境变量）
skillhub install-self      # 安装 SkillHub 自身到各平台
skillhub sync              # 所有资源自动落地
```

### 日常维护

```bash
skillhub list                        # 查看清单
skillhub list --kind mcp             # 只看 MCP
skillhub status                      # 查看上游更新
skillhub sync                        # 拉取所有更新
skillhub update my-skill             # 更新单个
skillhub remove my-skill             # 从清单移除
skillhub remove my-skill --uninstall # 连本地文件一起删
skillhub platforms                   # 查看平台配置
skillhub doctor                      # 排查问题
```

---

## MCP Server 注册

### Cursor

编辑 `~/.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "skillhub": {
      "command": "skillhub-mcp"
    }
  }
}
```

### Claude Code

```bash
claude mcp add skillhub -- skillhub-mcp
```

重启 IDE 后 Agent 可用以下工具：

| 工具 | 作用 |
|------|------|
| `list_items(kind?)` / `list_skills()` | 列出资源 |
| `list_platforms()` | 查看平台配置 |
| `publish_local_skill(path, kind?, private?)` | 发布 |
| `set_skill_visibility(name, private)` | 修改可见性 |
| `add_external_skill(github_url, kind?, mcp_config?)` | 登记 |
| `add_mcp_server(name, github_url, command?, url?)` | 登记 MCP 服务器 |
| `remove_skill(name, uninstall?)` | 移除 |
| `sync_skills(only?, kind?, platform?)` | 同步 |
| `update_skill(name)` | 更新单个 |
| `skill_status(kind?)` | 查看更新状态 |

对 Agent 说：

> "把桌面上的 my-pdf-skill 发布到 GitHub，公开仓库"
> "添加 https://github.com/anthropics/skills 的 pdf 子目录"
> "添加一个 GitHub MCP 服务器"
> "同步所有资源"
> "只把 skill 同步到 Claude Code"

---

## registry.yaml 格式

清单是单一事实源，v2 格式：

```yaml
version: 2
items:
  - name: memory-analysis-dev
    kind: skill
    repo: https://github.com/your-name/cursor-skill-memory-analysis-dev
    source: owned
    subdir: ""
    ref: main
    description: "..."

  - name: github-mcp
    kind: mcp
    repo: https://github.com/someone/mcp-server-github
    source: external
    subdir: ""
    ref: main
    mcp_config:
      command: npx
      args: ["-y", "@modelcontextprotocol/server-github"]
      env:
        GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_TOKEN}"

  - name: anthropic-pdf
    kind: skill
    repo: https://github.com/anthropics/skills
    source: external
    subdir: pdf
    ref: main
```

旧版 v1 格式（`version: 1` + `skills` 列表）读取时自动升级为 v2。

---

## 仓库结构

```
SkillHub/
├── registry.yaml                 # 资源清单
├── pyproject.toml
├── examples/config.example.toml
├── skillhub/                     # 主包
│   ├── cli.py                    # skillhub CLI
│   ├── mcp_server.py             # skillhub-mcp MCP server
│   ├── publisher.py              # 本地资源 → GitHub 仓库
│   ├── installer.py              # registry → 各平台目录
│   ├── mcp_installer.py          # 读写 mcp.json
│   ├── platforms.py              # 多平台抽象
│   ├── registry.py               # registry.yaml 读写 + v1→v2 迁移
│   ├── validator.py              # 资源校验
│   ├── github_client.py          # PyGithub 封装
│   ├── git_ops.py                # git 子进程封装
│   ├── config.py                 # 配置和 token 加载
│   ├── models.py                 # Pydantic 数据模型
│   └── __init__.py
├── tests/                        # pytest 测试
└── .github/workflows/ci.yml      # CI
```

---

## 安全说明

- **token 永远不入库** — `registry.yaml` 只存 HTTPS URL；push/pull 时 token 临时注入，操作完立即还原
- **配置文件权限** — `skillhub init` 写完后尝试 `chmod 600`
- **可脱离配置文件** — 只设 `SKILLHUB_GITHUB_TOKEN` 环境变量也能用

---

## 开发

```bash
pip install -e ".[dev]"
ruff check skillhub tests
pytest -q
```

CI 在 Linux + Windows × Python 3.10 / 3.11 / 3.12 上运行。

---

## 许可证

MIT，见 [LICENSE](LICENSE)。
