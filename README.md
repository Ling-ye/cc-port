# SkillHub

> 你的 AI 编程助手资源中央仓库 —— 一行命令把所有 skill、MCP 服务器配置和规则同步到新电脑。

SkillHub 是一个开源的 AI 编程助手资源注册中心，同时提供 **CLI** 和 **MCP server** 两种入口。它支持 **Cursor** 和 **Claude Code** 两大平台，管理三种资源类型：

| 资源类型 | 说明 | 安装位置 |
|---------|------|---------|
| **Skill** | 包含 `SKILL.md` 的 Agent 技能目录 | 各平台 skills 目录 |
| **MCP Server** | MCP 服务器配置（command/args/env） | 注入各平台 mcp.json |
| **Rule** | 编码规则和约定文件 | 各平台 rules 目录 |

核心解决三件事：

1. **发布本地资源**：把本地目录一键发布成一个独立的 GitHub 仓库。
2. **登记第三方资源**：把别人写好的优秀 skill、MCP 配置记到清单里。
3. **环境一键迁移**：换电脑时 `skillhub sync` 一条命令，所有资源自动落地到 Cursor 和 Claude Code。

所有资源清单存在 [`registry.yaml`](registry.yaml) 中，跟着 git 历史一起走。

---

## 它能做什么

| 能力 | 对应命令 / MCP 工具 | 说明 |
|---|---|---|
| 发布本地资源到 GitHub | `skillhub publish <path> [--kind]` | 校验 → 建仓 → push → 写入清单 |
| 修改可见性 | `skillhub set-visibility <name> public\|private` | 一键公开/转私有 |
| 登记第三方 skill | `skillhub add <github-url>` | 只记 URL 和 ref |
| 登记 MCP 服务器 | `skillhub add <url> --kind mcp --mcp-config '{...}'` | 记录 MCP 配置 |
| 同步全部到本机 | `skillhub sync` | 安装到所有启用的平台 |
| 按平台同步 | `skillhub sync --platform cursor` | 只同步到指定平台 |
| 按类型同步 | `skillhub sync --kind mcp` | 只同步 MCP 配置 |
| 更新单个 | `skillhub update <name>` | 强制同步单条 |
| 查看更新状态 | `skillhub status` | 对比本地/远端 commit |
| 列出已注册的 | `skillhub list [--kind]` | 支持按类型过滤 |
| 查看平台配置 | `skillhub platforms` | 显示所有平台和目录 |
| 从清单中移除 | `skillhub remove <name> [--uninstall]` | 可选连本地文件一起删 |
| 体检环境 | `skillhub doctor` | 检查 git / token / 目录 / 平台 |
| 安装 SkillHub 自身 | `skillhub install-self` | 复制到所有启用平台 |

---

## 安装

前置要求：Python >= 3.10、本机已安装 `git`。

```bash
git clone https://github.com/<你的用户名>/SkillHub.git
cd SkillHub
pip install -e .
```

安装完成后，会得到两个可执行命令：

- `skillhub`：终端 CLI
- `skillhub-mcp`：MCP server（供 Cursor / Claude Code 使用）

> Windows 用户如果提示「skillhub 不是有效命令」，把 pip 提示的 `Scripts` 目录加到 `PATH`，或暂时用 `python -m skillhub.cli ...` 代替。

---

## 首次配置

```bash
skillhub init
```

按提示填写：

- **GitHub Personal Access Token**：用于建仓和 push，需要 `repo` 权限。
- **owner**：新建仓库的所有者；留空则使用 token 对应的账户。
- **repo_prefix**：新建仓库的统一前缀，默认 `cursor-skill-`。
- **default_private**：新建仓库默认是否私有。
- **平台选择**：选择启用 Cursor 和/或 Claude Code，并配置各自的目录路径。

配置文件写到 `~/.config/skillhub/config.toml`，可参考 [`examples/config.example.toml`](examples/config.example.toml)。

### 推荐：把 token 放环境变量

```bash
# Linux / macOS
export SKILLHUB_GITHUB_TOKEN=ghp_xxxxxxxx

# Windows PowerShell
$env:SKILLHUB_GITHUB_TOKEN = "ghp_xxxxxxxx"
```

环境变量优先级高于配置文件。

---

## 多平台支持

SkillHub 同时支持 Cursor 和 Claude Code。在 `config.toml` 中配置：

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

`skillhub sync` 会把资源安装到所有启用的平台。你也可以用 `--platform` 参数限制目标平台：

```bash
skillhub sync --platform cursor      # 只同步到 Cursor
skillhub sync --platform claude-code  # 只同步到 Claude Code
```

---

## 日常使用

### 1. 发布一个本地 skill 到 GitHub

```bash
skillhub publish D:\dev\my-skill
# 或指定类型
skillhub publish D:\dev\my-mcp-server --kind mcp --mcp-config '{"command":"node","args":["server.js"]}'
```

### 2. 登记第三方资源

```bash
# Skill
skillhub add https://github.com/someone/awesome-cursor-skill

# MCP 服务器
skillhub add https://github.com/someone/mcp-server --kind mcp \
  --mcp-config '{"command":"npx","args":["-y","@someone/mcp-server"]}'

# 仓库里某个子目录
skillhub add https://github.com/anthropics/skills --subdir pdf --ref main
```

### 3. 换电脑一键迁移（核心场景）

```bash
git clone https://github.com/<你的用户名>/SkillHub.git
cd SkillHub
pip install -e .
skillhub init                 # 粘贴 token，选择平台
skillhub install-self         # 安装到所有启用的平台
skillhub sync                 # 所有资源自动落地
```

然后注册 MCP server（见下文），重启 IDE 即可。

### 4. 维护

```bash
skillhub list                         # 看清单
skillhub list --kind mcp              # 只看 MCP 配置
skillhub status                       # 看哪些有上游更新
skillhub sync                         # 一键拉所有更新
skillhub update my-skill              # 只更新单个
skillhub remove my-skill              # 从清单删除
skillhub remove my-skill --uninstall  # 同时清理本地文件
skillhub platforms                    # 查看平台配置
skillhub doctor                       # 排查环境问题
```

---

## 在 Cursor 里直接对 Agent 说话调用

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

## 在 Claude Code 里使用

```bash
claude mcp add skillhub -- skillhub-mcp
```

重启后 Agent 多出以下工具：

- `list_items(kind?)` / `list_skills()` —— 列出资源
- `list_platforms()` —— 查看平台配置
- `publish_local_skill(path, kind?, private?, ...)` —— 发布
- `set_skill_visibility(name, private)` —— 修改可见性
- `add_external_skill(github_url, kind?, mcp_config?, ...)` —— 登记
- `add_mcp_server(name, github_url, command?, url?, ...)` —— 登记 MCP 服务器
- `remove_skill(name, uninstall?)` —— 移除
- `sync_skills(only?, kind?, platform?)` —— 同步
- `update_skill(name)` —— 更新单个
- `skill_status(kind?)` —— 查看更新状态

例子：

> 「把我桌面上的 my-pdf-skill 发布到 GitHub，公开仓库」
> 「添加 https://github.com/anthropics/skills 这个仓库的 pdf 子目录作为 skill」
> 「添加一个 GitHub MCP 服务器」
> 「同步所有资源到本机」
> 「只把 skill 同步到 Claude Code」

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
    install_dir: ""
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

旧版 v1 格式（`version: 1` + `skills` 列表）会在读取时自动升级为 v2。

---

## 仓库结构

```
SkillHub/
├── registry.yaml                 # 你的资源清单
├── pyproject.toml
├── examples/config.example.toml
├── skillhub/                     # 主包
│   ├── cli.py                    # `skillhub` 命令
│   ├── mcp_server.py             # `skillhub-mcp` MCP server
│   ├── publisher.py              # 本地资源 -> GitHub 仓库
│   ├── installer.py              # registry -> 各平台目录
│   ├── mcp_installer.py          # 读写 mcp.json 配置
│   ├── platforms.py              # 多平台抽象 (Cursor, Claude Code)
│   ├── registry.py               # registry.yaml 读写 + v1->v2 迁移
│   ├── validator.py              # 资源校验 (SKILL.md / MCP config / 规则)
│   ├── github_client.py          # PyGithub 封装
│   ├── git_ops.py                # git 子进程封装
│   ├── config.py                 # 配置 & token 加载
│   ├── models.py                 # pydantic 数据模型
│   └── __init__.py
├── tests/                        # pytest 单测
└── .github/workflows/ci.yml      # CI
```

---

## 安全说明

- **token 永远不入库**：`registry.yaml` 里只存 https URL；push/pull 时 token 临时拼到 URL 上，操作完立刻还原。
- **配置文件权限**：`skillhub init` 写完后会尝试 `chmod 600`。
- **可完全脱离配置文件**：只设 `SKILLHUB_GITHUB_TOKEN` 环境变量也能用。

---

## 开发

```bash
pip install -e ".[dev]"
ruff check skillhub tests
pytest -q
```

CI 在 Linux + Windows x Python 3.10 / 3.11 / 3.12 上跑同样的检查。

---

## 许可证

MIT，见 [LICENSE](LICENSE)。
