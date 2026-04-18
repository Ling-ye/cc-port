# LPM (LingyePluginMarketplace)

> AI 编程助手的资源中央仓库 -- 一行命令把 skill、MCP 服务器、规则同步到任意一台新电脑。

LPM 是一个开源工具，同时提供 **CLI** 和 **MCP server** 两种入口，跨 **Cursor**、**Claude Code**、**Windsurf**、**Codex** 等 AI 编程平台管理三种资源：

| 资源类型 | 说明 | 安装位置 |
|---------|------|---------|
| **Skill** | 包含 `SKILL.md` 的 Agent 技能目录 | 各平台 skills 目录 |
| **MCP Server** | MCP 服务器配置（command/args/env） | 注入各平台 mcp.json |
| **Rule** | 编码规则和约定文件 | 各平台 rules 目录 |

核心场景：

1. **发布** -- 把本地目录一键发布为独立的 GitHub 仓库
2. **登记** -- 把别人的 skill / MCP 配置记入清单
3. **迁移** -- 换电脑时 `lpm sync` 一条命令，所有资源自动落地
4. **项目发现** -- `lpm link` 让项目中的 AI agent 自动识别已收集的 skill
5. **搜索** -- `lpm search` 在本地清单和 GitHub 上搜索可用资源

所有资源清单存在 [`registry.yaml`](registry.yaml) 中，跟着 git 一起走。

---

## 功能一览

| 能力 | 命令 / MCP 工具 | 说明 |
|------|----------------|------|
| 发布本地资源 | `lpm publish <path> [--kind]` | 校验 -> 建仓 -> push -> 写入清单 |
| 修改可见性 | `lpm set-visibility <name> public\|private` | 公开或转私有 |
| 登记第三方 skill | `lpm add <github-url>` | 记 URL + ref |
| 登记 MCP 服务器 | `lpm add <url> --kind mcp --mcp-config '{...}'` | 记 MCP 配置 |
| 搜索资源 | `lpm search [query] [--tag --remote]` | 本地过滤 + GitHub 搜索 |
| 链接到项目 | `lpm link [--tag --only]` | 生成 skill 索引 + symlink |
| 取消链接 | `lpm unlink` | 清理项目中的 LPM 链接 |
| 同步全部 | `lpm sync` | 安装到所有启用平台 |
| 按平台同步 | `lpm sync --platform cursor` | 只装到指定平台 |
| 按类型同步 | `lpm sync --kind mcp` | 只同步 MCP 配置 |
| 更新单个 | `lpm update <name>` | 强制同步一条 |
| 查看状态 | `lpm status` | 对比本地/远端 commit |
| 列出清单 | `lpm list [--kind]` | 按类型过滤 |
| 健康检查 | `lpm check [--prune]` | 检查仓库可达性 |
| 查看平台 | `lpm platforms` | 显示平台和路径 |
| 移除 | `lpm remove <name> [--uninstall]` | 可选同时删本地文件 |
| 环境检查 | `lpm doctor` | 检查 git / token / 平台 |
| 安装自身 | `lpm install-self` | 复制到所有启用平台 |

---

## 安装

前置要求：Python >= 3.10、`git` 已装好。

```bash
git clone https://github.com/Ling-ye/SkillHub.git
cd SkillHub
pip install -e .
```

安装后有两个命令：

- `lpm` -- 终端 CLI
- `lpm-mcp` -- MCP server（Cursor / Claude Code 调用）

> Windows 如果提示找不到命令，把 pip 提示的 `Scripts` 目录加到 `PATH`，或用 `python -m lpm.cli ...` 代替。

---

## 首次配置

### 三步搞定

```bash
# 1. 生成配置文件（默认只启用 Cursor）
lpm init

# 同时启用 Claude Code：
lpm init --claude-code
```

`init` 会在 `~/.config/lpm/config.toml` 生成一个带注释的模板：

```toml
[github]
token = ""                     # 填你的 GitHub PAT，或改用环境变量
owner = ""                     # 填你的 GitHub 用户名（如 "Ling-ye"）
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
#    Windows: %USERPROFILE%\.config\lpm\config.toml
#    macOS/Linux: ~/.config/lpm/config.toml

# 3. 验证配置
lpm doctor
```

### token 也可以用环境变量

```bash
# Linux / macOS
export LPM_GITHUB_TOKEN=ghp_xxxxxxxx

# Windows PowerShell
$env:LPM_GITHUB_TOKEN = "ghp_xxxxxxxx"
```

环境变量优先于 config.toml 中的 `token` 字段。两种方式选一种即可。

---

## 多平台支持

config.toml 中可以配置多个平台。内置预设包括 Cursor、Claude Code、Windsurf、Codex，也可自定义任意平台：

```toml
[platforms.cursor]
enabled = true
skills_dir = "~/.cursor/skills"
mcp_json = "~/.cursor/mcp.json"

[platforms.claude-code]
enabled = true
skills_dir = "~/.claude/skills"
mcp_json = "~/.claude.json"

# 自定义平台 -- 填好路径即可，无需改代码
[platforms.my-tool]
enabled = true
skills_dir = "~/my-tool/skills"
mcp_json = "~/my-tool/mcp.json"
```

`lpm sync` 安装到所有启用平台，也可以用 `--platform` 限定：

```bash
lpm sync --platform cursor
lpm sync --platform claude-code
```

---

## 日常使用

### 发布本地 skill

```bash
# 最短路径 -- 跳过询问，用默认可见性
lpm publish D:\dev\my-skill -y

# 明确指定公开/私有
lpm publish D:\dev\my-skill --public
lpm publish D:\dev\my-skill --private

# 带元数据发布
lpm publish D:\dev\my-skill --private -y \
  --tag python --tag fastapi --category software-dev \
  --version 1.0.0 --author Lingye --license MIT

# 发布 MCP 服务器配置
lpm publish D:\dev\my-mcp --kind mcp \
  --mcp-config '{"command":"node","args":["server.js"]}'
```

实际示例（已验证）：

```bash
lpm publish D:\Code\yourself-skill-master-uploadtest --private -y
# Published create-yourself (skill) -> https://github.com/Ling-ye/cursor-skill-create-yourself.git (private, created)
```

发布后 `registry.yaml` 自动更新，记得 commit 并 push：

```bash
cd <LPM仓库目录>
git add registry.yaml
git commit -m "add create-yourself skill"
git push
```

### 登记第三方资源

```bash
# Skill
lpm add https://github.com/someone/awesome-skill --tag python --category productivity

# MCP 服务器
lpm add https://github.com/someone/mcp-server --kind mcp \
  --mcp-config '{"command":"npx","args":["-y","@someone/mcp-server"]}'

# 仓库子目录
lpm add https://github.com/anthropics/skills --subdir pdf --ref main
```

### 搜索资源

```bash
# 搜索本地注册表
lpm search python
lpm search --tag testing --kind skill
lpm search --category software-dev

# 同时搜索 GitHub 上包含 SKILL.md 的仓库
lpm search fastapi --remote
```

### 项目中链接 skill（自动发现）

```bash
cd <你的项目目录>

# 链接所有已安装的 skill 到当前项目
lpm link

# 只链接特定标签或类型
lpm link --tag python
lpm link --only my-skill

# 清理链接
lpm unlink
```

`lpm link` 会在项目中创建：
- `.cursor/rules/lpm-skills.md` -- Cursor Rule 索引文件，AI agent 读取后自动知道有哪些 skill 可用
- `.cursor/skills/<name>` -- 指向全局安装目录的 symlink

这样 AI 在处理项目时会自动参考可用的 skill，遇到匹配场景时主动加载对应的 SKILL.md。

### 换电脑一键迁移

```bash
git clone https://github.com/Ling-ye/SkillHub.git
cd SkillHub
pip install -e .
lpm init              # 生成配置模板
# 编辑 config.toml 填好 token 和 owner（或设环境变量）
lpm install-self      # 安装 LPM 自身到各平台
lpm sync              # 所有资源自动落地
```

### 日常维护

```bash
lpm list                        # 查看清单
lpm list --kind mcp             # 只看 MCP
lpm status                      # 查看上游更新
lpm check                       # 检查仓库可达性
lpm check --prune               # 自动移除不可达的条目
lpm sync                        # 拉取所有更新
lpm update my-skill             # 更新单个
lpm remove my-skill             # 从清单移除
lpm remove my-skill --uninstall # 连本地文件一起删
lpm platforms                   # 查看平台配置
lpm doctor                      # 排查问题
```

---

## MCP Server 注册

### Cursor

编辑 `~/.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "lpm": {
      "command": "lpm-mcp"
    }
  }
}
```

### Claude Code

```bash
claude mcp add lpm -- lpm-mcp
```

重启 IDE 后 Agent 可用以下工具：

| 工具 | 作用 |
|------|------|
| `list_items(kind?)` / `list_skills()` | 列出资源 |
| `list_platforms()` | 查看平台配置 |
| `publish_local_skill(path, kind?, private?, tags?, category?)` | 发布 |
| `set_skill_visibility(name, private)` | 修改可见性 |
| `add_external_skill(github_url, kind?, mcp_config?, tags?)` | 登记 |
| `add_mcp_server(name, github_url, command?, url?)` | 登记 MCP 服务器 |
| `check_items(kind?, prune?)` | 检查仓库可达性 |
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

清单是单一事实源，v3 格式（v1/v2 自动迁移）。示例：

```yaml
version: 3
items:
  - name: create-yourself
    kind: skill
    repo: https://github.com/Ling-ye/cursor-skill-create-yourself.git
    source: owned
    subdir: ''
    ref: main
    description: "Why distill others when you can distill yourself? ..."
    tags: [python, ai]
    category: productivity
    author: Lingye
    private: true

  - name: github-mcp
    kind: mcp
    repo: https://github.com/someone/mcp-server-github
    source: external
    ref: main
    mcp_config:
      command: npx
      args: ["-y", "@modelcontextprotocol/server-github"]
      env:
        GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_TOKEN}"
    tags: [github, git]

  - name: anthropic-pdf
    kind: skill
    repo: https://github.com/anthropics/skills
    source: external
    subdir: pdf
    ref: main
```

新增的元数据字段（均可选，空值不写入文件）：

| 字段 | 说明 | 示例 |
|------|------|------|
| `version` | 语义化版本 | `"1.2.0"` |
| `author` | 作者 | `"Lingye"` |
| `tags` | 标签列表 | `[python, testing]` |
| `category` | 分类 | `"software-dev"` |
| `license` | SPDX 许可证 | `"MIT"` |
| `private` | 缓存的 GitHub 可见性 | `true` |

---

## 仓库结构

```
SkillHub/
├── registry.yaml                 # 资源清单（真实数据）
├── pyproject.toml
├── examples/config.example.toml
├── lpm/                          # 主包
│   ├── cli.py                    # lpm CLI
│   ├── mcp_server.py             # lpm-mcp MCP server
│   ├── publisher.py              # 本地资源 -> GitHub 仓库
│   ├── installer.py              # registry -> 各平台目录
│   ├── linker.py                 # 项目级 skill 链接和自动发现
│   ├── mcp_installer.py          # 读写 mcp.json
│   ├── platforms.py              # 多平台抽象（可扩展）
│   ├── registry.py               # registry.yaml 读写 + v1->v2->v3 迁移
│   ├── validator.py              # 资源校验
│   ├── github_client.py          # PyGithub 封装
│   ├── git_ops.py                # git 子进程封装（GIT_ASKPASS 安全认证）
│   ├── config.py                 # 配置和 token 加载
│   ├── models.py                 # Pydantic 数据模型（含元数据）
│   └── __init__.py
├── tests/                        # pytest 测试
└── .github/workflows/ci.yml      # CI
```

---

## 安全说明

- **token 永远不入库** -- `registry.yaml` 只存 HTTPS URL；git 操作通过 `GIT_ASKPASS` 传递 token，不写入 `.git/config`
- **配置文件权限** -- `lpm init` 写完后尝试 `chmod 600`
- **可脱离配置文件** -- 只设 `LPM_GITHUB_TOKEN` 环境变量也能用
- **不要在聊天中发送 token** -- 如果不慎暴露，立即到 GitHub Settings 撤销并重新生成

---

## 开发

```bash
pip install -e ".[dev]"
ruff check lpm tests
pytest -q
```

CI 在 Linux + Windows x Python 3.10 / 3.11 / 3.12 上运行。

---

## 许可证

MIT，见 [LICENSE](LICENSE)。
