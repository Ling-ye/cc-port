# LPM (LingyePluginMarketplace)

LPM is an open-source command-line tool for managing personal AI coding resources across machines and agent platforms.

It separates the public tool from private user data:

- **LPM repository**: open-source tool code, documentation, and examples.
- **Resource repository**: your private Git repository containing selected skills, rules, prompts, MCP configs, plugins, and `registry.yaml`.

This design lets LPM itself stay public while your personal resources remain private and portable.

## Features

- Collect third-party open-source resources by reference, without copying upstream projects.
- Upload local resources into your private resource repository.
- Restore selected resources on a new machine with `lpm sync`.
- Support multiple resource kinds: `skill`, `mcp`, `rule`, `prompt`, and `plugin`.
- Keep MCP secrets out of Git by storing environment placeholders such as `${GITHUB_TOKEN}`.
- Manage the private resource repository through `lpm resource ...` commands.
- Provide both CLI and MCP server entry points.

## Repository Model

The public LPM repository should not contain personal resources. A separate private repository stores user data:

```text
<your-resource-repo>/
  README.md
  registry.yaml
  skills/
  rules/
  prompts/
  mcp/
  plugins/
  .claude-plugin/
    plugin.json
```

The resource repository name is user-defined. If none is provided, LPM uses `LingyeAIResources` as the default name and `~/<repo_name>` as the local path.

## Installation

Requirements:

- Python 3.10+
- Git
- A GitHub token if you want LPM to create or push a private resource repository

Install from a local checkout:

```bash
git clone https://github.com/Ling-ye/LingyePluginMarketplace.git
cd LingyePluginMarketplace
pip install -e .
```

Generate the user config:

```bash
lpm init
```

Configure a GitHub token either in `~/.config/lpm/config.toml` or through an environment variable:

```bash
# macOS / Linux
export LPM_GITHUB_TOKEN=ghp_xxxxx

# Windows PowerShell
$env:LPM_GITHUB_TOKEN = "ghp_xxxxx"
```

## Quick Start

Create or connect a private resource repository:

```bash
lpm resource init --name MyAIResources
```

Or bind an existing one:

```bash
lpm resource use https://github.com/<you>/MyAIResources.git
```

Collect an open-source skill by reference:

```bash
lpm collect https://github.com/juliusbrussee/caveman
```

Collect a resource located in a GitHub subdirectory:

```bash
lpm collect https://github.com/anthropics/skills/tree/main/pdf
```

Upload a local resource into your private repository:

```bash
lpm upload D:\MySkills\review-skill
```

If automatic type detection is not enough, specify the type:

```bash
lpm upload D:\Prompts\review.md --type prompt
lpm upload D:\MCP\lark.yaml --type mcp
```

After `collect` or `upload`, LPM asks whether to push the private resource repository. You can also choose explicitly:

```bash
lpm collect https://github.com/juliusbrussee/caveman --push
lpm upload D:\MySkills\review-skill --no-push
```

## Restore on a New Machine

Install LPM and connect your private resource repository:

```bash
git clone https://github.com/Ling-ye/LingyePluginMarketplace.git
cd LingyePluginMarketplace
pip install -e .
lpm init
lpm resource use https://github.com/<you>/MyAIResources.git
lpm resource pull
lpm sync
```

By default, `lpm sync` restores skills only. Other resource kinds are opt-in because they can modify tool configuration or agent behavior:

```bash
lpm sync --include-mcp
lpm sync --include-rule
lpm sync --include-prompt
lpm sync --include-plugin
lpm sync --all-kinds
```

## Resource Commands

```bash
lpm resource init --name MyAIResources
lpm resource use <path-or-git-url>
lpm resource status
lpm resource pull
lpm resource push
```

`lpm resource init` creates or connects a private GitHub repository, generates the standard directory structure, creates a `README.md`, and writes the resource repository configuration.

## Daily Commands

```bash
lpm collect <github-url-or-tree-url>
lpm upload <local-path>
lpm sync
lpm list
lpm status
lpm check
lpm remove <name>
```

Examples:

```bash
lpm collect https://github.com/juliusbrussee/caveman
lpm collect https://github.com/anthropics/skills/tree/main/pdf
lpm upload D:\MySkills\review-skill --type skill
lpm upload D:\Prompts\review.md --type prompt
lpm resource push
```

## Resource Type Detection

LPM detects resource type automatically when possible:

| Type | Detection |
| --- | --- |
| `skill` | Directory contains `SKILL.md` |
| `plugin` | Directory contains `.claude-plugin/plugin.json` or `.codex-plugin/plugin.json` |
| `mcp` | `mcp.yaml`, `mcp.yml`, or `mcp.json` |
| `rule` | Markdown file or directory name contains `rule` / `rules` |
| `prompt` | Markdown file that is not detected as a rule |

Use `--type` when the type cannot be inferred.

## Configuration

LPM reads `~/.config/lpm/config.toml` by default. The important sections are:

```toml
[github]
token = ""
owner = ""

[resources]
repo_name = "LingyeAIResources"
repo_url = ""
local_path = ""
branch = "main"

[platforms.cursor]
enabled = true
skills_dir = "~/.cursor/skills"
mcp_json = "~/.cursor/mcp.json"
rules_dir = ""
```

`LPM_RESOURCE_HOME` can override the local resource repository path.

## MCP Server

LPM also provides an MCP server:

```bash
lpm-mcp
```

For Cursor, add it to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "lpm": {
      "command": "lpm-mcp"
    }
  }
}
```

For Claude Code:

```bash
claude mcp add lpm -- lpm-mcp
```

## Security Notes

- Keep your resource repository private if it contains personal resources or metadata.
- Do not commit real tokens or secrets.
- MCP `env` values are stored as placeholders such as `${API_KEY}`.
- GitHub authentication uses `GIT_ASKPASS` so tokens are not written to `.git/config`.
- The public LPM repository ignores `registry.yaml`, `skills/`, `rules/`, `prompts/`, `mcp/`, `plugins/`, and `.claude-plugin/`.

## Development

Install in editable mode:

```bash
pip install -e .
```

Run static checks:

```bash
ruff check lpm
python -m compileall -q lpm
```

## License

MIT. See [LICENSE](LICENSE).
