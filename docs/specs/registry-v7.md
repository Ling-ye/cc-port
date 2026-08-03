# Registry v7（历史格式）

Registry v7 已被不兼容的、工具中立的 [Registry v1](registry-v1.md) 取代。

CC Port 的普通加载器不再读取或迁移 v7。远端仓库审计器只识别可解析的 v7，以便用户显式确认后依据当前实体资源覆盖为 v1；旧外部引用和 CC Port 专属设置不会迁移。需要查阅旧字段时请使用 Git 历史，本文件不再定义有效运行时契约。
