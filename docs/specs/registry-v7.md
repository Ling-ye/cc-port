# Registry v7 规格

## 插件双轨

- `plugin` 仍是资源类型，不新增第二套插件清单。
- 新插件条目可携带 `plugin` 规格；`track=content` 表示私库保存用户确认拥有的源码，`track=reference` 表示私库只保存第三方安装意图。
- `reference` 不保存 marketplace cache、npm cache、managed 内容、运行数据或凭据。
- 插件规格固定包含 `platform`、`plugin_id`、`origin`、`installations`；OpenCode 自有内容可额外保存该插件独有的 `dependencies`。
- `origin.selector` 保存原声明策略，`observed_version` 只记录扫描结果，不得替代 selector。
- 扫描器无法可靠获知 selector 时必须保留注册表中的原策略；只有显式添加或能证明来源的扫描结果才能改写 selector。

```yaml
version: 7
items:
  - name: codex-marketplace-chrome-openai-bundled
    kind: plugin
    source: external
    plugin:
      track: reference
      platform: codex
      plugin_id: chrome
      origin:
        type: marketplace
        marketplace: openai-bundled
        source: openai-bundled
        selector: ""
      observed_version: 26.707.72221
      installations:
        - scope: user
          enabled: true
```

## 身份与作用域

- 不同平台或来源的同名插件默认是不同逻辑资源，不自动跨平台关联。
- 默认名称由平台、来源和插件标识组成；超过名称限制或发生冲突时追加来源摘要的稳定短哈希。
- 同平台、同来源的多个 `user`、`project`、`local`、`managed` 实例聚合到一个插件资源。
- `project` 与 `local` 实例只保存规范化 Git remote 和仓库内相对根；local content 的来源身份同时包含仓库内插件相对路径，绝对路径只存在于本机 LPM 配置。
- 无 Git remote 的项目实例可以扫描和展示，但不能保存到远端注册表。
- `managed` 实例只读，不能由 LPM 安装、启用、禁用或卸载。
- marketplace 来源必须保存可移植的 marketplace 名称或远端 URL/Git 身份；本机 runtime/cache 绝对路径不得进入注册表。

## 兼容与验证

- v6 条目读取后升级注册表版本，但不为旧 `plugin` 条目猜测轨道、平台或来源；缺少 `plugin` 规格的旧条目继续使用 v6 内容语义。
- `source=external` 的插件引用可以没有 GitHub `repo`，但必须提供完整且可验证的 reference 规格。
- `content` 条目仍要求私库 `path` 或 owned `repo`，并且首次上传必须携带源码归属确认。
- `reference` 指纹由规范化来源、selector 和期望安装实例计算，不读取 cache 内容。
- 已安装且可写的 reference 下载可以对齐启用状态；缺失实例返回手工安装指引，managed 实例只报告组织策略要求。
- YAML 仍按 `(kind, name)` 排序；空插件可选字段不写入。
