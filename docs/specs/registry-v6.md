# Registry v6 规格

## 身份

- 资源唯一身份是 `(kind, name)`，稳定字符串形式为 `<kind>:<name>`。
- 名称只要求在同一 `kind` 内唯一；不同类型允许同名。
- 新接口必须同时传入 `kind` 和 `name`。
- 旧名称查询仅在匹配唯一时成功；多类型同名必须返回歧义错误。

## 安装名称

- `platform_install_dirs[platform]` 优先于旧 `install_dir`，旧 `install_dir` 优先于资源 `name`。
- 安装名称必须是由小写字母、数字和连字符组成的安全单路径段。
- registry 允许不同资源解析到相同目标名称，但下载必须在目标碰撞消除前阻断。

## 迁移

- v5 项目保留原有字段并补充空的 `platform_install_dirs`。
- v5 中的名称天然唯一，因此升级到复合键不会丢失项目。
- 管理标记新增 `resource_key`；读取仍兼容只含 `resource` 与 `kind` 的旧标记。

## 序列化

- registry 写回版本固定为 `6`。
- 空的 `platform_install_dirs` 不写入 YAML。
- 项目排序固定为 `(kind, name)`，避免仅因加载顺序产生无关差异。
