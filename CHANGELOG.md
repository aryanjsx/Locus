# Changelog

All notable changes to this project are documented here. Phase 2 credibility remediation references the adversarial audit (20 violations) and Fix prompts 00–13.

## [0.2.0-alpha] — 2026-07-08

### Security & correctness (audit remediation)

- **SafetyGate consolidation** — Single canonical `SafetyGate` in `aura/security/safety_gate.py`; removed duplicate core implementation. Voice path uses `check()` with 8s confirmation timeout; CLI path uses `request()` with audit logging on all branches.
- **Destructive-action enforcement** — Canonical `DESTRUCTIVE_ACTIONS` frozenset in `aura/schemas/command.py`. `CommandEngine.execute()` re-derives `is_destructive` before dispatch; voice utterances for shutdown/restart/log_off/close_app route through SafetyGate (`tests/test_voice_destructive_path.py`).
- **Schema consolidation** — Single `IntentObject`/`IntentType` in `aura/schemas/intent.py`; single `CommandPlan`/`ExecutionResult` in `aura/schemas/command.py`.
- **LLM streaming pipeline** — `GENERAL_KNOWLEDGE`, `CODE_GENERATION`, `DEV_TASK`, `PROJECT_CONTEXT`, and `REALTIME_QUERY` route to `LLM_ONLY` executor → `main.py` `_stream_to_tts()` for audible output.
- **IntentRouter two-tier classification** — Fast regex for obvious intents; LLM fallback with timeout/retry for ambiguous input; `UNKNOWN` fallback per spec.
- **SessionController wired** — Instantiated in `main.py`; manages wake-word pause/resume and inactivity timeout.
- **Mic contention fix** — Shared `mic_lock` in STT and wake-word paths; SessionController serializes active sessions.
- **Config filename** — Loader fallback resolves `config.example.yaml` on fresh clone.
- **TTS temp-file cleanup** — `try/finally` unlink in Edge/Piper synthesis paths.
- **Screenshot transient files** — Temp PNG with `transient: True`; not persisted per spec.
- **Shell timeout** — `ShellExecutor` reads `shell.timeout` from config (default 120s).
- **Dead code removed** — Deleted `aura/core/voice_executor.py` (SafetyGate bypass).
- **Wake-word persona** — All user-facing strings use "Hey Kommy"; zero "Hey AURA" references.

### Verification

- Fix 13 final pass: **20/20 violations FIXED** (`scripts/fix13_verify.py`)
- Test suite: **620+ tests passing** including `test_phase2_audit_part1.py`, `test_phase2_audit_part2.py`, `test_destructive_gate.py`, `test_voice_destructive_path.py`
- Static security sweep: `shell=True` = 0, `eval(`/`exec(` = 0 in `aura/` production code

### Documentation

- README rewritten as accurate status report (not aspirational marketing)
- Added `docs/decisions/naming.md` (Kommy product name vs AURA architecture ADR)
- Added `AURA_ENGINEERING_SPEC.md` to repository

## [0.1.0] — Phase 1

- CLI command registry, sandbox, permissions, audit chain, system plugin
- Isolated worker subprocess IPC

[0.2.0-alpha]: https://github.com/aryanjsx/AURA/compare/v0.1.0...v0.2.0-alpha
