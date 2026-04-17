# SkillHub

> 你的 Cursor Agent Skill 中央仓库 —— 一行命令把所有 skill 同步到新电脑。

SkillHub 是一个开源的 Cursor Skill 注册中心，同时提供 **CLI** 和 **MCP server** 两种入口。它专门解决三件事：

1. **发布本地 skill**：把本地的 skill 目录一键发布成一个独立的 GitHub 仓库。
2. **登记第三方 skill**：把别人写好的优秀 skill（GitHub 链接）记到清单里，方便统一安装/更新。
3. **环境一键迁移**：换电脑或开新工作环境时，只需 clone 本仓库 + 一条 `skillhub sync`，所有 skill 自动落地到 `~/.cursor/skills/` 并保持随时可更新。

所有 skill 的清单存在仓库内的 [`registry.yaml`](registry.yaml) 中，跟着 git 历史一起走，开源也安全（token 不入库）。

---

## 它能做什么

| 能力 | 对应命令 / MCP 工具 | 说明 |
|---|---|---|
| 把本地 skill 发布到 GitHub | `skillhub publish <path>` | 校验 SKILL.md → **询问 public/private** → 在你的账户下建仓 → push → 写入清单 |
| 修改已发布 skill 的可见性 | `skillhub set-visibility <name> public\|private` | 一键把 owned skill 仓库公开/转私有 |
| 登记一个第三方 skill | `skillhub add <github-url>` | 只记 URL 和 ref，不复制源码 |
| 在新机器上安装全部 skill | `skillhub sync` | 遍历清单，缺失则 clone，存在则 pull |
| 单独更新某个 skill | `skillhub update <name>` | 强制同步单条记录 |
| 查看上游有没有更新 | `skillhub status` | 对比本地 / 远端 commit |
| 列出当前注册的 skill | `skillhub list` | 同时显示是否已安装、仓库可见性 |
| 从清单中移除 | `skillhub remove <name> [--uninstall]` | 可选连本地文件一起删 |
| 体检环境 | `skillhub doctor` | 检查 git / token / 目录权限 / 配置文件 |
| 把 SkillHub 自己作为 skill 装到本机 | `skillhub install-self` | 让 Agent 学会何时调用 SkillHub 的 MCP 工具 |

在 Cursor 聊天里也能直接让 Agent 调用这些能力（见下文 MCP 接入）。

---

## 安装

前置要求：Python ≥ 3.10、本机已安装 `git`。

```bash
git clone https://github.com/<你的用户名>/SkillHub.git
cd SkillHub
pip install -e .
```

安装完成后，会得到两个可执行命令：

- `skillhub`：终端 CLI
- `skillhub-mcp`：MCP server（供 Cursor 使用）

> Windows 用户如果提示「skillhub 不是有效命令」，把 pip 提示的 `Scripts` 目录加到 `PATH`，或暂时用 `python -m skillhub.cli ...` 代替。

---

## 首次配置

```bash
skillhub init
```

按提示填写：

- **GitHub Personal Access Token**：用于建仓和 push，需要 `repo` 权限。
- **owner**：新建 skill 仓库的所有者；留空则使用 token 对应的账户。
- **repo_prefix**：新建仓库的统一前缀，默认 `cursor-skill-`，例如最终生成 `cursor-skill-pdf-tools`。
- **default_private**：新建仓库默认是否私有。
- **install target**：skill 安装到哪里，默认 `~/.cursor/skills`。

配置文件写到 `~/.config/skillhub/config.toml`，可参考 [`examples/config.example.toml`](examples/config.example.toml)。

### 推荐：把 token 放环境变量（让仓库可以放心开源）

不要把 token 提交到任何仓库。最干净的做法是只在 `init` 时填空，然后在 shell 里导出：

```bash
# Linux / macOS
export SKILLHUB_GITHUB_TOKEN=ghp_xxxxxxxx

# Windows PowerShell
$env:SKILLHUB_GITHUB_TOKEN = "ghp_xxxxxxxx"
```

环境变量优先级高于配置文件。

---

## 日常使用

### 1. 发布一个本地 skill 到 GitHub

假设你刚写好 `D:\dev\my-skill\SKILL.md`：

```bash
skillhub publish D:\dev\my-skill
```

SkillHub 会：

1. 校验 `SKILL.md` 的 frontmatter（`name` / `description`）。
2. **交互式询问仓库可见性**（public 还是 private），默认值取自配置文件 `default_private`。
3. 在 `<owner>/cursor-skill-<name>` 下创建一个新仓库（已存在则复用），按你的选择设置可见性。
4. 在本地目录 `git init` + `commit` + `push`。
5. 把这个 skill 写入 `registry.yaml`，类型标记为 `owned`。

成功后会显示实际可见性，例如：

```
Published my-skill -> https://github.com/alice/cursor-skill-my-skill.git (public, created)
```

#### 跳过交互或在脚本中使用

```bash
# 直接指定可见性，跳过询问
skillhub publish ./my-skill --public
skillhub publish ./my-skill --private

# 跳过询问且使用配置文件中的 default_private
skillhub publish ./my-skill --yes

# 其他可选参数
skillhub publish ./my-skill --name custom-name --description "..." --private
```

#### 修改已发布 skill 的可见性

如果之后想把 skill 仓库从公开改成私有（或反过来）：

```bash
skillhub set-visibility my-skill private
skillhub set-visibility my-skill public
```

或者用 `publish` 重新指定 `--private/--public` 加 `--update-visibility`：

```bash
skillhub publish ./my-skill --private --update-visibility
```

如果不加 `--update-visibility`，遇到现有仓库可见性不一致时会**报错并拒绝静默修改**，避免把私有内容意外公开。

### 2. 登记一个别人写好的第三方 skill

只记一条 URL，不下载源码、不建仓：

```bash
# 完整仓库就是一个 skill
skillhub add https://github.com/someone/awesome-cursor-skill

# 仓库里某个子目录才是 skill
skillhub add https://github.com/anthropics/skills --subdir pdf --ref main
```

### 3. 换电脑 / 新环境一键迁移（核心场景）

```bash
git clone https://github.com/<你的用户名>/SkillHub.git
cd SkillHub
pip install -e .
skillhub init                 # 粘贴 token
skillhub install-self         # 让 Agent 学会"什么时候用 SkillHub"
skillhub sync                 # 把 registry 里的全部 skill 装到 ~/.cursor/skills
```

最后再到 `~/.cursor/mcp.json` 注册一次 MCP server（见下文），重启 Cursor，整套技能环境就还原好了。以后任何一台新机器都重复以上几步即可。

> **`install-self` 是什么？** SkillHub 自己也是一个 skill —— 它把仓库根目录的 [`SKILL.md`](SKILL.md) 拷贝到 `~/.cursor/skills/skillhub/`，这样 Agent 在你说「把这个 skill 发布出去」「同步我的技能」时，能自动选对工作流并调用 SkillHub 的 MCP 工具。

### 4. 维护

```bash
skillhub list           # 看清单
skillhub status         # 看哪些有上游更新
skillhub sync           # 一键拉所有更新
skillhub update my-skill        # 只更新单个
skillhub remove my-skill        # 从清单删除
skillhub remove my-skill --uninstall   # 同时清理 ~/.cursor/skills/my-skill
skillhub doctor         # 排查环境问题
```

---

## 在 Cursor 里直接对 Agent 说话调用

把 SkillHub 注册成 MCP server，编辑 `~/.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "skillhub": {
      "command": "skillhub-mcp"
    }
  }
}
```

重启 Cursor 后，Agent 多出以下工具，可以直接对话触发：

- `list_skills` —— 列出当前注册的 skill
- `publish_local_skill(path, name?, description?, private?, update_visibility?)` —— **必须传 `private`**，Agent 应先问用户公开还是私有
- `set_skill_visibility(name, private)` —— 修改已发布 skill 仓库的公开/私有状态
- `add_external_skill(github_url, name?, subdir?, ref?, description?)`
- `remove_skill(name, uninstall?)`
- `sync_skills(only?)`
- `update_skill(name)`
- `skill_status`

例子（直接在 Cursor 聊天里）：

> 「把我桌面上的 my-pdf-skill 发布到 GitHub，公开仓库」
> 「把 my-pdf-skill 改成私有」
> 「添加 https://github.com/anthropics/skills 这个仓库的 pdf 子目录作为 skill」
> 「同步所有 skill 到本机」

---

## registry.yaml 格式

清单是单一事实源，结构很简单：

```yaml
version: 1
skills:
  - name: memory-analysis-dev
    repo: https://github.com/your-name/cursor-skill-memory-analysis-dev
    source: owned          # owned 表示是你 publish 的，external 表示是第三方
    subdir: ""             # SKILL.md 在仓库内的相对路径，空 = 根目录
    ref: main              # 跟踪的分支 / tag / commit
    install_dir: ""        # 留空则用 name 作为安装目录名
    description: "..."     # 自动同步自 SKILL.md
  - name: anthropic-pdf
    repo: https://github.com/anthropics/skills
    source: external
    subdir: pdf            # 只装这个子目录（自动 sparse-checkout）
    ref: main
```

`owned` 和 `external` 在安装时一视同仁：都通过 `git clone` / `git pull` 落到 `~/.cursor/skills/<name>/`。当 `subdir` 不为空时，会用 sparse-checkout 只展开那一个子目录。

---

## 仓库结构

```
SkillHub/
├── registry.yaml                 # 你的 skill 清单（提交并 push 即可分享）
├── pyproject.toml
├── examples/config.example.toml
├── skillhub/                     # 主包
│   ├── cli.py                    # `skillhub` 命令
│   ├── mcp_server.py             # `skillhub-mcp` MCP server
│   ├── publisher.py              # 本地 skill -> GitHub 仓库
│   ├── installer.py              # registry -> ~/.cursor/skills/
│   ├── registry.py               # registry.yaml 读写
│   ├── validator.py              # SKILL.md 校验
│   ├── github_client.py          # PyGithub 封装
│   ├── git_ops.py                # git 子进程封装（push/pull/sparse）
│   ├── config.py                 # 配置 & token 加载
│   ├── models.py                 # pydantic 数据模型
│   └── __init__.py
├── tests/                        # pytest 单测
└── .github/workflows/ci.yml      # Linux + Windows × Py 3.10/3.11/3.12
```

---

## 安全说明

- **token 永远不入库**：`registry.yaml` 里只存 https URL；push/pull 时 token 临时拼到 URL 上，操作完立刻 `git remote set-url` 还原成纯净 URL。
- **配置文件权限**：`skillhub init` 写完后会尝试 `chmod 600`。
- **可完全脱离配置文件**：只设 `SKILLHUB_GITHUB_TOKEN` 环境变量也能用，方便 CI 场景。

---

## 开发

```bash
pip install -e ".[dev]"
ruff check skillhub tests
pytest -q
```

CI 在 Linux + Windows × Python 3.10 / 3.11 / 3.12 上跑同样的检查。

---

## 许可证

MIT，见 [LICENSE](LICENSE)。
