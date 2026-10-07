# Session Manager

A dependency-free Python CLI for inspecting and deleting local Codex and Kimi Code sessions. Python 3.10+ is supported; see the Zstandard requirements below for compressed Codex logs.

## Install and run

```bash
python -m pip install -e .
sessionmanager
```

The interactive flow is **platform → project (`cwd`) → sessions**. Explicit `--platform` and `--project` skip the corresponding initial prompts.

- Enter a session number, or `v 2`, to inspect the complete first prompt/response and file list.
- Enter `d 1,3-5` to select sessions for deletion, then review every file, the total size, and the confirmation prompt. A plain number only opens details.
- `0` goes back exactly one level; `q` or an empty line quits. EOF exits cleanly; Ctrl-C returns 130.
- `n` / `p` change pages. `/text` filters the project list by path, or the session list by ID/title/first prompt/response. `/` clears the filter.
- Session numbers retain their meaning within the displayed project while searching/paging. After deletion or cancellation, the current project is refreshed; an empty project returns to the project list.
- Listings show short IDs, titles, update times in UTC, sizes, archive status, and question/answer previews. JSON retains complete `first_prompt` and `first_response` strings.

```bash
sessionmanager --platform codex
sessionmanager --platform kimi --project /path/to/project
sessionmanager --platform codex --page-size 10 --search "migration" --sort size
```

## Scriptable commands

```bash
# Platform summary, project summary, then sessions inside a project:
sessionmanager --json
sessionmanager --platform codex --json
sessionmanager --platform codex --project /path/to/project --json

# Inspect one full, exact ID (short display IDs are not accepted):
sessionmanager --platform codex --project /path/to/project --details UUID --json

# Review a complete deletion plan, then explicitly confirm:
sessionmanager --platform codex --project /path/to/project --delete-id UUID --dry-run --json
sessionmanager --platform codex --project /path/to/project --delete-id UUID --yes --json

# Repeat --delete-id for a batch; index selection is also available:
sessionmanager --platform kimi --project /path/to/project --delete-id session_UUID1 --delete-id session_UUID2 --yes
sessionmanager --platform codex --project /path/to/project --delete 1,3-5 --dry-run
```

`--delete`, `--delete-id`, and `--details` require both `--platform` and `--project`. `--platform all` includes both providers. IDs must match exactly; `platform:ID` can disambiguate. Prefer IDs in automation because indexes can change when sessions are updated. `--search` and `--sort updated|created|size` apply before index selection; default ordering is newest update first, with deterministic ties. Codex update time uses the rollout file's modification time.

JSON and non-interactive deletion require `--yes` unless `--dry-run` is set; they never read a confirmation from stdin. JSON deletion returns one object with `status`, `sessions`, `file_count`, `bytes`, `blockers`, and, after execution, per-session `results` containing `deleted` and `failed` paths. A blocked dry-run returns 1. Diagnostics go to stderr, leaving stdout parseable. Session timestamps include explicit UTC fields `created_at` and `updated_at`; the existing `date` field is retained.

Use `--project '(unknown project)'` to select sessions without a known working directory. Without a project, non-interactive listing returns project summaries; without a platform it returns platform summaries.

Exit codes: **0** success / user cancellation; **1** blocked deletion, partial failure, or an I/O failure; **2** invalid arguments, selection, or missing explicit confirmation; **130** Ctrl-C.

## Storage discovery and deletion boundaries

| Provider | Default root | Official override | Session Manager override |
| --- | --- | --- | --- |
| Codex | `~/.codex` | `CODEX_HOME` | `SESSIONMANAGER_CODEX_ROOTS` |
| Kimi Code | `~/.kimi-code` | `KIMI_CODE_HOME` | `SESSIONMANAGER_KIMI_ROOTS` |

Official overrides point to the **data home**; Session Manager overrides point to **session directories** and take precedence. Multiple roots use the OS path separator (`:` on Linux/macOS, `;` on Windows). Empty path-list entries are ignored.

```bash
CODEX_HOME=/data/codex sessionmanager --platform codex
KIMI_CODE_HOME=/data/kimi-code sessionmanager --platform kimi
SESSIONMANAGER_CODEX_ROOTS=/copy/.codex/sessions sessionmanager --platform codex
SESSIONMANAGER_KIMI_ROOTS=/copy/.kimi-code/sessions sessionmanager --platform kimi
```

### Codex

Supports `sessions/` and `archived_sessions/`, plain `.jsonl`, compressed `.jsonl.zst`, and reverted-thread filenames containing a separate rollout ID. Recognition requires a rollout filename plus matching valid `session_meta` metadata. UUID substrings in unrelated filenames do not establish ownership. Only validated rollout files and their exact `.jsonl.lock` sidecars enter the deletion set; duplicate plain/compressed copies are grouped.

A standard `sessions` / `archived_sessions` root (or a subdirectory of one) automatically includes its companion tree so archived and cross-project history references cannot be overlooked. `history_base.thread_id` identifies a **rollout ID**, which may differ from the stable session ID after a revert. Sessions referenced by unselected sessions cannot be deleted. A batch deletes dependants before ancestors; a failed dependant continues to protect its ancestor. Cyclic references are refused.

Zstandard decoding uses Python 3.14's `compression.zstd`, otherwise the system `libzstd` shared library, otherwise the `zstd` executable. No third-party Python runtime package is required. If no decoder is available, compressed logs are skipped with a diagnostic and Codex deletion is disabled for that scan. The same conservative block applies when candidate rollouts have unreadable/invalid metadata or a history reference cannot be interpreted. Unknown files are never added to the deletion set. Invalid JSONL records are skipped without crashing the scan.

### Kimi Code

Targets the Node.js `@moonshot-ai/kimi-code` layout:

```text
$KIMI_CODE_HOME/sessions/<workDirKey>/session_<uuid>/
  state.json
  agents/main/wire.jsonl
  agents/agent-*/wire.jsonl
  agents/main/plans/
  tasks/
  logs/
  cron/
  upcoming-goals.json
```

Reads the whole `state.json` (including pretty-printed JSON), validates the directory/metadata ID, normalizes millisecond or ISO timestamps to UTC, and reads previews only from `agents/main/wire.jsonl`. Streaming text parts are joined; the complete persisted assistant message is preferred. All regular files inside the validated session directory, including subagent logs, plans, task output, and queued goals, belong to its explicit file set. Only empty owned directories are pruned afterward. Multiple copies of the same ID are grouped, and every copy is shown in the deletion plan.

For compatibility, default discovery also examines `~/.kimi/sessions` and `~/.kimi/session` for this validated `session_<uuid>` layout. It does **not** claim support for arbitrary UUID-only directories or the old Python CLI's one-file layouts; migrate those with the official `kimi migrate` command first.

### Safety and scope

Configuration, credentials, shared databases/indexes (including `session_index.jsonl`), global input history/logs, and shared date/workspace directories are preserved. This is session-file deletion, not global-index rewriting or a guarantee of removing every trace from upstream tools.

Deletion re-scans the original roots, checks references again, compares the complete file set, and checks file identity/size/mtime before unlinking. Changed/replaced files cause refusal and require refreshing the selection. Symlinks and linked directory trees are excluded. Close sessions in their originating CLI before deleting: these checks do not lock the other application's writers or make a multi-file deletion atomic. Custom roots must include every relevant session tree; references in data homes outside the configured scan cannot be checked.

## Compatibility evidence and tests

The fixtures are synthetic, modeled on Codex **0.160.1** and Kimi Code **2.1.1**. They contain no user session data. Sources:

- [Codex rollout filenames](https://github.com/openai/codex/blob/rust-v0.160.1/codex-rs/rollout/src/rollout_file_name.rs), [compression](https://github.com/openai/codex/blob/rust-v0.160.1/codex-rs/rollout/src/compression.rs), and [history reference index](https://github.com/openai/codex/blob/rust-v0.160.1/codex-rs/rollout/src/rollout_reference_index.rs).
- [Kimi data locations](https://www.kimi.com/code/docs/en/kimi-code-cli/configuration/data-locations.html), [metadata writer](https://github.com/MoonshotAI/kimi-code/blob/f67e6398fb3210ad8ace970e2dfd5bcc984ed61f/packages/migration-legacy/src/sessions/state-writer.ts), and [wire records](https://github.com/MoonshotAI/kimi-code/blob/f67e6398fb3210ad8ace970e2dfd5bcc984ed61f/packages/migration-legacy/src/sessions/turn-structure.ts).

```bash
python -m pip install -e '.[test]'
python -m pytest
python -m sessionmanager --help
```

Compressed fixture tests require one of the decoder backends above. See [AGENTS.md](AGENTS.md) for project conventions.

## Uninstall

```bash
python -m pip uninstall sessionmanager
# Or, if installed with uv:
uv tool uninstall sessionmanager
```

Uninstalling removes only Session Manager. It does not delete Codex or Kimi data.
