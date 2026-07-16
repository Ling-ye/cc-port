# Git 凭据边界规格

## 目标

GitHub token 或其他 Git HTTPS 凭据不得进入仓库配置、远端 URL、命令参数、临时脚本、错误消息、任务记录或用户可见日志。

## 凭据传递

- clone、fetch、push、ls-remote 和 probe 使用同一凭据适配层。
- HTTPS token 只通过 Git 子进程环境中的临时配置传递。
- 不生成包含 token 字面量的 AskPass 文件。
- SSH 操作不注入 HTTPS token。
- 子进程结束后不保留任何凭据文件。

## 输入与错误

- branch、ref 和 remote 参数必须作为独立参数传递，并拒绝控制字符和以 `-` 开头的值。
- Git 命令错误中的 URL userinfo 必须脱敏。
- 异常不得回显认证头、环境变量值或 token。
- `.git/config` 中的 remote URL 必须是无凭据 URL。

## 验收标准

- 包含空格、引号、百分号、管道符和与号的 token 可以安全使用。
- token 不出现在 `subprocess.run` 参数、Git remote、临时文件和异常文本中。
- 私有 HTTPS 仓库的 probe、clone、fetch 和 push 使用一致的认证语义。
- SSH URL 不受 HTTPS 凭据配置影响。
