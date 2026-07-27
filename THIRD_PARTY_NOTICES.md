# Third-party notices

CC Port is distributed under the MIT License, but it depends on third-party
software under other open-source licenses. The dependency manifests and lock
files remain the authoritative record of the versions used to build a release.

The direct runtime dependencies declared by the v0.5.0 source tree were
reviewed before release:

| Ecosystem | Dependency | License |
| --- | --- | --- |
| Python | Typer | MIT |
| Python | Rich | MIT |
| Python | Pydantic | MIT |
| Python | PyYAML | MIT |
| Python | PyGithub | GNU LGPL |
| Python | python-frontmatter | MIT |
| Python | FastMCP | Apache-2.0 |
| Python | tomli | MIT |
| npm | Tauri API and plugins | MIT OR Apache-2.0 |
| npm | Lucide React | ISC |
| npm | React and React DOM | MIT |
| Rust | Tauri, Tauri Build, and Tauri plugins | MIT OR Apache-2.0 |
| Rust | Serde and serde_json | MIT OR Apache-2.0 |

This summary does not replace the license text shipped by each dependency.
For the complete dependency graph, exact versions, source locations, and
license files, use:

- `pyproject.toml` and the installed Python package metadata;
- `desktop/package-lock.json` and each package's `package.json`;
- `desktop/src-tauri/Cargo.lock` and each crate's published metadata.

The corresponding upstream projects provide their source and license text:

- [Typer](https://github.com/fastapi/typer)
- [Rich](https://github.com/Textualize/rich)
- [Pydantic](https://github.com/pydantic/pydantic)
- [PyYAML](https://github.com/yaml/pyyaml)
- [PyGithub](https://github.com/PyGithub/PyGithub)
- [python-frontmatter](https://github.com/eyeseast/python-frontmatter)
- [FastMCP](https://github.com/jlowin/fastmcp)
- [tomli](https://github.com/hukkin/tomli)
- [Tauri](https://github.com/tauri-apps/tauri)
- [Tauri plugins](https://github.com/tauri-apps/plugins-workspace)
- [Lucide](https://github.com/lucide-icons/lucide)
- [React](https://github.com/facebook/react)
- [Serde](https://github.com/serde-rs/serde)
