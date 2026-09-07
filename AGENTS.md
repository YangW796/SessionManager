# AGENTS.md

## Project purpose

Session Manager is a dependency-free Python CLI for listing and deleting local Codex and Kimi Code sessions.

## Engineering rules

- Keep platform-specific path and metadata handling in `sessionmanager/providers.py`. Kimi Code currently stores sessions under `~/.kimi-code/sessions/<workspace>/session_<uuid>/`; include `state.json`, all agent wire logs, task files, and logs in that session's file set.
- Session listings should expose a short preview of the first user prompt and first agent response, while JSON output keeps the complete preview strings in `first_prompt` and `first_response`.
- A provider must return an explicit file set for every session. Do not delete a parent directory unless it is itself a UUID-owned session directory and is empty after file deletion.
- Never remove platform-wide configuration, databases, indexes, or a shared date directory.
- Treat malformed or unknown files as non-session data and skip them.
- Keep CLI behavior scriptable: stable exit codes, `--json`, `--dry-run`, and explicit confirmation for destructive operations.
- The CLI is intentionally two-level: group sessions by project (`cwd`) first, then display/select sessions within one project. Keep `--project PATH` working for non-interactive use.
- When no `--platform` is provided in a terminal, the CLI first selects `codex` or `kimi`; explicit `--platform` remains the scriptable path.
- Add fixture-based tests for every new storage layout or association rule.

## Verification

Run `python -m pytest` before submitting changes. Manually inspect `python -m sessionmanager --json` against a disposable fixture or a copied session directory before changing discovery rules.

## Adding a platform

Implement a `Provider`, add it to `providers()`, document its default paths and override variable in `README.md`, and test both discovery and complete deletion. Do not couple providers to the CLI.
