# Session Manager

Session Manager is a small local CLI for quickly inspecting and removing coding-agent sessions. The first supported platforms are OpenAI Codex and Kimi Code.

## Install and run

Python 3.10 or newer is required. From this directory:

```bash
python -m pip install -e .
sessionmanager
```

Running `sessionmanager` without arguments first asks you to choose an agent platform, then a project, and finally sessions. Session numbers are only meaningful inside the selected project. Select sessions interactively, or specify the platform and project directly for scripts:

```bash
sessionmanager
sessionmanager --platform codex
sessionmanager --platform kimi --project /path/to/project
sessionmanager --platform codex --project /path/to/project --delete 1,3-5
sessionmanager --platform kimi --project /path/to/project --delete 2 --yes
sessionmanager --platform codex --project /path/to/project --delete 1 --dry-run
sessionmanager --platform kimi --json
```

In interactive mode, enter `0` to go back one level from the project or session list. Session deletion accepts a number, comma-separated numbers, or ranges such as `1,3-5`; invalid input is rejected and can be re-entered.

没有提供 `--project` 时，交互终端会要求先选择项目；非交互环境会只输出项目列表并提示使用 `--project PATH`。没有提供 `--platform` 时，`--json` 输出平台列表；指定平台后输出项目列表，指定 `--project` 后输出该项目中的 sessions。

每个 session 会显示首个用户问题和首段 agent 回答，便于删除前确认内容；`--json` 中对应字段为 `first_prompt` 和 `first_response`。

Deletion always prints the selected sessions and asks for confirmation unless `--yes` is supplied. Only files identified as belonging to the selected session are removed. Platform configuration, global indexes, unrelated sessions, and shared date directories are preserved.

## Storage discovery

The standard locations are `~/.codex/sessions`, `~/.codex/archived_sessions`, `~/.kimi-code/sessions`, `~/.kimi/sessions`, and `~/.kimi/session`. To use another location, set a platform-specific path list separated by the OS path separator (`:` on Linux/macOS, `;` on Windows):

```bash
SESSIONMANAGER_CODEX_ROOTS=/path/to/codex/sessions sessionmanager
SESSIONMANAGER_KIMI_ROOTS=/path/to/kimi/sessions sessionmanager
```

Codex sessions are grouped by the UUID in their session metadata or rollout filename. Kimi supports both UUID directories and UUID-named files. Unknown files are skipped conservatively; no broad content search is used to guess ownership.

## Uninstall

If installed with pip, remove the command and package with:

```bash
python -m pip uninstall sessionmanager
```

If installed as a uv tool, use:

```bash
uv tool uninstall sessionmanager
```

卸载只移除 Session Manager 本身，不会删除 Codex/Kimi 的 session 数据；session 数据请通过本工具显式选择删除。

## Development

```bash
python -m pytest
python -m sessionmanager --help
```

See [AGENTS.md](AGENTS.md) for repository conventions and extension guidance.
