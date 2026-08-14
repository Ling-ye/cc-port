# Registry v1 规格

## 1. 目标与职责边界

`registry.yaml` 是工具中立、可移植的资源成员清单。仓库中的实体文件和目录是内容事实；没有内容副本的引用型资源以 `source` 为事实。CC Port 只是该清单的一个消费者，其他工具可以独立读取、生成或修改它。

Registry 只回答三个问题：

1. 仓库声明了哪些逻辑资源；
2. 每个资源的稳定身份是什么；
3. 资源内容位于当前仓库的哪个路径，或者来自哪个外部来源。

Registry 不保存以下信息：

- 描述、版本、作者、许可证和标签；
- 内容哈希、可达性、检查时间、仓库可见性和观测版本；
- 删除历史、安装状态和项目状态；
- 平台白名单、安装别名、插件启用范围等 CC Port 专属意图；
- 本机 profile id、`tool_id`、Windows/WSL 环境、用户目录和原生目标路径；
- MCP 启动配置的第二份副本；
- 真实凭据或凭据化 URL。

派生元数据从当前资源内容读取。本机观测状态进入本机缓存。CC Port 专属意图进入可选的 `cc-port.yaml`。删除资源就是删除清单项；历史由 Git 和本机操作记录承担。

## 2. 文档结构

规范文档只有两个顶层字段：

```yaml
version: 1
resources:
  - kind: skill
    name: code-review
    path: skills/code-review

  - kind: skill
    name: upstream-review
    source:
      type: git
      locator: https://github.com/example/skills
      revision: 4f3c2d1
      subpath: skills/code-review

  - kind: plugin
    name: browser-tools
    source:
      type: marketplace
      locator: openai-bundled/browser-tools
      revision: latest
```

未知顶层字段属于 schema 错误。`resources` 必须是列表，可以为空。

### 2.1 资源字段

| 字段 | 必填 | 约束 |
| --- | --- | --- |
| `kind` | 是 | 小写安全标识符；1–64 字符，可使用字母、数字、`.`、`_`、`-` |
| `name` | 是 | 小写 slug；1–64 字符，以字母或数字开头，只使用字母、数字、`-` |
| `path` | 与 `source` 二选一 | 当前仓库内 POSIX 相对路径 |
| `source` | 与 `path` 二选一 | 外部来源对象 |

稳定身份是 `(kind, name)`，字符串形式为 `<kind>:<name>`。同一文档内身份必须唯一；两个资源也不能声明相同的非空 `path`。

`path` 必须满足：

- 不是绝对路径，也不是 Windows drive path；
- 不包含反斜杠、空段、`.` 或 `..`；
- 不以 `/` 结尾；
- 解析后不能越出仓库根目录；
- 路径及其任何祖先不能是符号链接或不受支持的 reparse point。

CC Port 原生认识 `skill`、`mcp`、`rule`、`prompt`、`plugin`、`instruction` 和 `memory`。其他安全 `kind` 必须原样往返保存并只读展示。已知类型不接受拼错或多余字段；未知类型可以携带额外数据，规范化重写时必须保留这些数据。新增已知 kind 不增加 Registry 字段，也不改变 v1 schema。

### 2.2 外部来源

`source` 的结构为：

```yaml
source:
  type: git
  locator: https://github.com/example/resources
  revision: 0123456789abcdef0123456789abcdef01234567
  subpath: skills/example
```

| 字段 | 必填 | 约束 |
| --- | --- | --- |
| `type` | 是 | 与 `kind` 相同的安全开放标识符 |
| `locator` | 是 | 非空、可移植定位符，不得内嵌用户名、密码或 Token |
| `revision` | 否 | 版本、commit、tag、range 或来源自定义 selector |
| `subpath` | 否 | 来源内部的安全 POSIX 相对路径 |

CC Port 原生支持 `git`、`npm` 和 `marketplace`。未知来源类型不是 Registry 健康错误；CC Port 原样展示，但不执行安装或写入。仓库一致性审计只检查结构、路径安全和凭据泄漏，不联网探测外部来源的实时可达性。

## 3. 内容布局与元数据

自动发现只扫描七个约定根目录的直接子项：

```text
skills/*        -> skill
mcp/*           -> mcp
rules/*         -> rule
prompts/*       -> prompt
plugins/*       -> plugin
instructions/*  -> instruction
memories/*      -> memory
```

直接子文件或目录按相应类型验证；Skill 必须通过 `SKILL.md` 规则，MCP 配置位于 `mcp/<name>/mcp.json|yaml|yml`，其他类型复用 CC Port 的现有内容验证器。审计不递归发现资源内部目录，也不扫描未知根目录。

显式登记的安全任意相对路径仍然有效，不要求位于约定目录。未知 `kind` 的内容路径只检查安全性与存在性，不解释其内部格式。

已登记资源内部内容变化但仍有效时，不构成 Registry 漂移。MCP 配置只存在于资源文件中；Registry 不保存 `mcp_config`。

## 4. Canonical 序列化

规范化输出必须：

- 固定写入 `version: 1`；
- 按 `(kind, name)` 升序排列资源；
- 保持字段的规范顺序；
- 省略空的可选来源字段；
- 使用 UTF-8、LF 和稳定 YAML 序列化；
- 不承诺保留注释、原字段顺序或人工排序；
- 多次加载和保存产生逐字节相同的结果。

## 5. 可选的 `cc-port.yaml`

`cc-port.yaml` 是 CC Port 的消费者 overlay，不属于 Registry。其他工具可以完全忽略它。

```yaml
version: 1
resources:
  skill:code-review:
    platforms:
      - cursor
    install_name: code-review
    install_names:
      cursor: review

  plugin:browser-tools:
    plugin:
      platform: codex
      plugin_id: browser-tools
      installations:
        - scope: user
          enabled: true

  plugin:review-tools:
    plugin:
      platform: claude-code
      plugin_id: review-tools
      marketplace: team-tools
      installations:
        - scope: user
          enabled: true
```

Overlay 用资源键关联设置。指向不存在资源的设置被忽略但保留，不反向创建 Registry 条目。资源来源和版本只写在 Registry 的 `source` 中；Claude Code Marketplace 的注册名写在插件 overlay 的 `marketplace`，不得用它替代来源定位符。插件 content/reference 轨道由 `path` 或 `source` 推导；观测版本只保存在本机。`instruction` 与 `memory` 的逻辑资源级工具兼容 allowlist 和通用安装别名可以进入 overlay；具体 Windows/WSL profile、用户目录、目标路径以及 `memory_install_names` project slot 映射只属于本机配置与操作计划，不能进入 Registry 或 overlay。

## 6. 仓库审计

每次刷新远端时，CC Port 对同一个 commit 建立只读快照并执行审计。刷新和审计不修改远端。`RemoteSnapshot.registry` 可以为空；Registry 不可用时，仓库仍可显示为已连接，但所有依赖远端清单的上传和安装动作必须阻断，本地扫描继续工作。

健康状态为：

| 状态 | 含义 |
| --- | --- |
| `healthy` | 文档有效且没有一致性问题 |
| `issues` | v1 可解析，但存在漂移或需要处理的问题 |
| `legacy` | 可解析的 v7，可依据当前实体内容覆盖为 v1 |
| `missing` | 非空仓库缺少 `registry.yaml`，只报告 |
| `invalid` | YAML、schema、文件类型或链接状态无效，只报告 |
| `unavailable` | 远端仓库无法取得 |

`registry.yaml` 缺失、YAML 无法解析、不是普通文件或本身是链接时，`repairable=false`。审计不读取 Git 历史，也不从目录自动重建这些状态。真正空白、尚未初始化的仓库由独立的仓库初始化流程创建 v1。

### 6.1 问题与默认动作

| 问题 | 默认动作 | 规则 |
| --- | --- | --- |
| `unregistered-resource` | `add` | 有效约定目录直接子项未登记 |
| `missing-resource` | `remove` | 只移除清单项，不恢复或删除内容 |
| `invalid-resource` | `keep` | 报告；用户可显式移除已登记项 |
| `unsafe-path` / `unsafe-link` / `invalid-source` | `keep` 且阻断 | 必须先修仓库或显式移除条目 |
| `duplicate-key` / `duplicate-path` | 阻断 | 用户必须选择保留的原始条目 |
| 未知 `kind` / `source.type` | 无错误 | 原样保留，只读展示 |
| `legacy-v7` | `replace` | 依据当前实体目录重建，并在确认前显示丢弃数量 |
| `missing-registry` / `invalid-yaml` / `registry-symlink` | 不可修复 | 仅诊断和手工处理指引 |

路径消失并出现同类型新目录时，固定表现为“移除旧项 + 新增新项”。审计不使用内容哈希或名称相似度推断重命名。

候选默认名称来自直接子项目录名或文件 stem 的 slug。空名称或身份冲突必须由用户填写唯一名称，不能自动猜测。

## 7. 修复计划与应用

检查阶段只生成计划和 YAML diff。计划至少绑定：

- 远端 commit；
- 原始 Registry SHA-256；
- 候选资源内容、安全链接状态和有效性指纹；
- 用户选择；
- 规范化结果；
- `plan_hash`。

应用阶段必须在新的临时 clone 中重新 fetch、扫描和生成同一计划。commit、Registry、候选路径、链接状态、内容有效性或选择结果任一变化都返回 `stale` 和完整新计划，不写入旧计划。

成功应用只写普通非链接的 `registry.yaml`，只暂存该文件，执行路径安全与秘密扫描，以固定标题 `修复资源索引` 创建一个提交，并普通推送到配置分支。禁止强推、自动合并和覆盖竞态。没有字节差异时返回 `unchanged`，不创建空提交。

修复永远不删除、移动、恢复或改写资源内容，也不修改 `cc-port.yaml`、README 或其他文件。

## 8. 接口

CLI：

```text
cc-port resource registry-check [--json]
cc-port resource registry-repair --dry-run [--choices choices.yaml] [--json]
cc-port resource registry-repair [--choices choices.yaml] [--json]
```

CLI 只检查或生成修复计划；兼容参数 `--yes` 不构成授权，也不得应用修复。机器调用必须使用
统一 JSON envelope。需要写入时，由用户在 Desktop 审阅，或使用 MCP
`registry_repair_plan` → Desktop approval → `registry_repair_apply`，并提交原样的
`operation_id`、`plan_hash`、choices 和 `approval_id`。

Desktop API：

- `registry_repair_plan` 接受 `choices`，返回 commit、状态、问题、选择、diff、`plan_hash` 和计数；
- `registry_repair_apply` 接受相同 `choices` 与必填 `operation_id`、`plan_hash`、`approval_id`，
  返回 `succeeded|unchanged|stale|blocked|failed`；
- `asset_inventory.registry_health` 返回状态、检查 commit 和问题计数。

Desktop 的“检查仓库”只打开计划。安全增删项默认选中；阻断项、v7 数据丢弃和最终 diff 必须由用户审阅。`missing`、`invalid` 和 Registry 链接状态不显示应用按钮。

## 9. v7 一次性覆盖

普通加载器只接受 v1，不实现 v5/v6/v7 字段迁移或双格式读取。审计器仅识别可解析的 v7，以便提供一次性“依据当前实体目录覆盖为 v1”操作：

- 不迁移旧外部引用；
- 不迁移平台别名、插件安装范围或其他 CC Port 专属字段；
- 不推断重命名或来源；
- 在确认前显示旧条目数、可重建实体数和丢弃数；
- 用户不确认时不写入。

旧数据仍可通过 Git 历史人工查看，但不属于运行时兼容契约。
