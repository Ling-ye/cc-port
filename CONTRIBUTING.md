# Contributing to CC Port

感谢你帮助改进 CC Port。项目采用“先明确问题，再实现最小改动”的方式维护，以避免多个 AI coding 平台之间的行为逐渐分叉。

## 先选择正确入口

- Bug：使用 [Bug report](https://github.com/Ling-ye/cc-port/issues/new?template=bug.yml)。
- 功能建议：使用 [Feature request](https://github.com/Ling-ye/cc-port/issues/new?template=feature.yml)。
- 安全漏洞：不要创建公开 Issue，按照 [SECURITY.md](SECURITY.md) 私密报告。
- 文档错字、失效链接和范围明确的小修复：可以直接提交 Pull Request。

## Issue 先行

以下变化必须先创建 Issue，得到维护者确认后再实现：

- 新平台或资源类型。
- CLI、MCP、Desktop API 或 Registry 格式变化。
- 同步、删除、所有权、凭据、备份或回滚语义变化。
- 新依赖、后台服务、遥测或网络请求。
- 大型界面重构。

Issue 应描述用户问题、可复现现状、期望结果、范围边界和替代方案。只描述解决方案而没有用户问题的提案可能会被关闭。

维护者确认方向不代表承诺合并。实现仍需满足规格、测试、安全边界和维护成本要求。

## 开发流程

1. Fork 仓库并从 `main` 创建短期分支。
2. 行为变化先更新 `docs/specs/`；架构决策更新 `docs/adr/`。
3. 为新增或修复行为添加测试。
4. 实现满足规格的最小改动。
5. 更新相关文档：
   - `README.md` 与 `README.en.md`
   - `CHANGELOG.md`
   - 快速开始、故障排查或开发文档
   - 相关规格和 ADR
6. 运行质量检查。
7. 提交一个范围清晰的 Pull Request。

环境准备和命令见[开发指南](docs/development.md)。

## 必须通过的检查

Python：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src/cc_port tests tools/packaging
```

前端：

```powershell
Push-Location .\desktop
npm test
npm run build
Pop-Location
```

修改 Tauri/Rust 时：

```powershell
cargo check --manifest-path .\desktop\src-tauri\Cargo.toml
```

如果本机无法运行某项检查，请在 Pull Request 中明确说明原因，不能把未运行写成已通过。

## Pull Request 要求

- 关联对应 Issue；小型文档修正除外。
- 说明改了什么、为什么改以及用户影响。
- 列出实际运行的验证命令和结果。
- 界面变化提供前后截图。
- 不混入无关重构、格式化或依赖升级。
- 不提交 Token、私有仓库内容、本机配置、构建产物或用户路径。
- 保持向后兼容；破坏性变化必须在 Issue 和规格中明确。

PR 默认以 squash merge 合并。维护者可能要求缩小范围或拆分提交。

## 文档风格

- 面向用户的文档使用直接、可验证的描述。
- 中文主文档与英文 README 的功能信息保持一致。
- 示例使用虚构仓库、路径、资源和凭据占位符。
- 不在公开文档中加入内部工作标记或未验证的性能结论。

## 行为准则

参与项目即表示你同意遵守 [Code of Conduct](CODE_OF_CONDUCT.md)。
