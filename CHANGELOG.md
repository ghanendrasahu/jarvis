# Changelog

All notable changes to JARVIS are documented in this file.
Versions follow [semantic versioning](https://semver.org/); tags must match the
`project.version` declared in `pyproject.toml` (enforced by the CD pipeline).

## [1.2.1] - 2026-08-24

Security and reliability release: closes the findings of the full code audit
(shell execution boundary, chat streaming/cancellation correctness, planner
fallback semantics, configuration drift) with extensive regression coverage.

### Security

- Hardened shell command execution against interpreter/script-host abuse:
  interpreters and script hosts (`python`, `pip`, `powershell`, `cmd`, `wsl`,
  `mshta`, …) are hard-denied even if explicitly added to
  `ALLOWED_SHELL_COMMANDS`; removed them from the default allowlist.
- Added structural PowerShell command validation: rejected command chaining,
  argument-position subexpressions/array expressions, script blocks, variable
  and environment indirection, static-member invocation, call-operator forms,
  escape sequences, and every redirection variant (`>`, `>>`, `2>`, `2>>`,
  `*>`, `2>&1`, `<`).
- Restricted `git` argument forms that execute external code (`ext::`
  transports, `-c` overrides, credential/config/bisect/filter-branch/hook/
  submodule-style subcommands).
- Blocked extension-disguised interpreters (`python.exe`, `pwsh.cmd`, …) from
  bypassing the hard-deny via configuration.
- Added extensive shell-security regression coverage (118 tests), including
  exact subprocess-invocation assertions for all legitimate commands.

### Chat Reliability & Correctness

- Fixed duplicated chat output when a provider failed mid-stream and the
  fallback/retry attempt concatenated its response onto the failed one.
- Improved STOP responsiveness: cancellation is now observed within ~0.2 s
  even when the provider read is stalled, without turning cancellation into an
  error, retry, or fallback; provider responses are still closed cleanly.
- Fixed image data URLs so the MIME type always matches the encoded payload
  (images are normalized to PNG and labeled `image/png`).
- Isolated chat streams by sequence: late events after **Clear** are dropped,
  never mutate UI state, and cleared responses are no longer written to chat
  history; events from superseded streams cannot leak into newer ones.
- Preserved typed chat input when JARVIS is busy: the composer is only cleared
  after a submission is accepted, with non-blocking busy feedback.
- Corrected planner fallback retries so every provider receives the same
  retry policy, and ensured total planner failure is recorded as a failed
  execution instead of inflating success metrics.

### Configuration & Runtime

- Rebuilt `.env.example` as valid UTF-8 (was UTF-16) and aligned it fully with
  the current Settings model: corrected stale defaults and wrong variable
  names, added missing provider/planner/chat settings, removed obsolete keys.
- Fixed `JARVIS_CHAT_FALLBACK_PROVIDERS` parsing: empty and comma-separated
  values now load correctly instead of crashing startup, and extras extend the
  chat fallback chain.
- Moved expensive voice-settings operations (Kokoro pipeline initialization,
  SAPI device enumeration) off the Qt GUI thread onto supervised workers;
  rapid setting changes can no longer apply stale results, and failures keep
  the previous working configuration.

### Testing

- Expanded automated coverage substantially across chat streaming/cancellation,
  configuration parity, planner fallback semantics, GUI event handling, voice
  settings concurrency, and shell security.
- Release candidate passes the complete suite (300 tests) plus Ruff, mypy, and
  CI quality gates.
