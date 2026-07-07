# 📋 AURA Roadmap

> A fully offline AI developer assistant — from command-line automation to voice-controlled coding workflows.

---

## Progress at a Glance

- [✔] **Phase 0** — Project Core (INFRA) (COMPLETED)
- [✔] **Phase 1** — Foundation (System Plugin) (COMPLETED)
- [✔] **Phase 2** — Voice + Intelligence Router (COMPLETED — 20/20 audit violations verified, Fix 13 pass 2026-07-08)
- [ ] Phase 3 — Dev Tools (Git + Docker) (planned)
- [ ] Phase 4 — Vision (Screen Understanding) (planned)
- [ ] Phase 5 — GUI Dashboard (planned)
- [ ] Phase 6 — Memory + RAG (planned)
- [ ] Phase 7 — Browser Automation (planned)
- [ ] Phase 8 — Integrations (planned)

---

## Phase 0 — Project Core (INFRA) ✅

**Status:** COMPLETED

The scaffolding. Cross-cutting infrastructure every later phase depends on — nothing user-facing, everything the rest of the system is built on.

| Deliverable | Module | Status |
|---|---|---|
| Event bus (thread-safe pub/sub) | `aura/core/event_bus.py` | ✅ Done |
| Config loader + env overrides | `aura/core/config_loader.py` | ✅ Done |
| Typed error hierarchy | `aura/core/errors.py` | ✅ Done |
| Structured `CommandResult` | `aura/core/result.py` | ✅ Done |
| `CommandSpec` schema + validator | `aura/core/schema.py` | ✅ Done |
| Parameter schema + size caps | `aura/core/param_schema.py` | ✅ Done |
| Trace-ID context var | `aura/core/tracing.py` | ✅ Done |
| Input / output abstractions | `aura/core/io.py` | ✅ Done |
| Structured JSON logger | `aura/core/logger.py` | ✅ Done |
| Interactive CLI bootstrap | `aura/cli.py` | ✅ Done |

---

## Phase 1 — Foundation (System Plugin) ✅

**Status:** COMPLETED

The foundation. A modular command-execution engine plus the full secure-execution layer — sandbox, permissions, audit chain, non-bypassable registry, isolated worker subprocess.

| Deliverable | Module | Status |
|---|---|---|
| Command dispatcher / router (text → Intent → registry) | `aura/runtime/router.py` | ✅ Done |
| Centralized path resolution (`~`, smart keywords, safety) | `plugins/system/executor.py` | ✅ Done |
| File operations (create, delete, rename, move, search) | `plugins/system/executor.py` | ✅ Done |
| Process management (run, list, kill) | `plugins/system/executor.py` | ✅ Done |
| System health checker (Python, Git, Node, Docker) | `plugins/system/executor.py` | ✅ Done |
| Project scaffolder (supports full paths) | `plugins/system/executor.py` | ✅ Done |
| Log file reader (supports full paths) | `plugins/system/executor.py` | ✅ Done |
| Intent system (text/LLM → structured action) | `aura/core/intent.py` | ✅ Done |
| Non-bypassable command registry | `aura/runtime/command_registry.py` | ✅ Done |
| Plugin loader + manifest enforcement | `aura/runtime/plugin_loader.py`, `aura/security/plugin_manifest.py` | ✅ Done |
| Isolated worker subprocess (JSON-line IPC) | `aura/worker/server.py`, `aura/runtime/worker_client.py` | ✅ Done |
| Filesystem sandbox + symlink escape block | `aura/security/sandbox.py` | ✅ Done |
| Shell argv allowlist + denylist policy | `aura/security/policy.py` | ✅ Done |
| PermissionLevel validator (source-capped) | `aura/security/permissions.py` | ✅ Done |
| Per-source sliding-window rate limiter | `aura/security/rate_limiter.py` | ✅ Done |
| Non-blocking confirmation safety gate | `aura/security/safety_gate.py` | ✅ Done |
| Tamper-evident hash-chained audit log + rotation sidecar | `aura/security/audit_log.py` | ✅ Done |
| Dynamic audit event registry | `aura/security/audit_events.py` | ✅ Done |
| LLM backend abstraction + Ollama stub | `aura/core/backends/` | ✅ Done |
| LLM brain (intent translator stub) | `aura/core/llm_brain.py` | ✅ Done |

Security properties verified end-to-end (runtime-probed, not documented-only):

- Exactly one execution entry point (`CommandRegistry.execute`) — no reachable dispatcher via reflection, closure walk, or `dir()` introspection.
- Worker boundary is JSON-only, size-capped, manifest-hash-bound, and schema-validated in both directions.
- Audit chain distinguishes rotation truncation from tampering via sidecar hash file.

---

## Phase 2 — Voice + Intelligence Router ✅

**Status:** COMPLETED · Verified 2026-07-08 (Fix 13: 20/20 audit violations fixed)

The transformation. AURA hears you, thinks locally, and speaks back.

| Deliverable | Tech | Module |
|---|---|---|
| Microphone listener + speech-to-text | Whisper (OpenAI) | `core/io.py` + new STT source |
| Intent parsing + command generation | Ollama (Llama 3) | `core/llm_brain.py`, `core/backends/` |
| Voice response synthesis | Piper TTS | `core/io.py` + new TTS sink |
| Prompt engineering for dev-task classification | — | `core/llm_brain.py` |
| End-to-end voice → action → response pipeline | — | `aura.py` (async main loop) |

---

## Phase 3 — Dev Tools (Git + Docker) ⏳

**Status:** Planned · **ETA:** Week 13

Real developer workflow automation — Git and Docker from voice or text.

| Deliverable | Tech | Module |
|---|---|---|
| Git automation (commit, push, branch, status) | GitPython | `plugins/git/` |
| AI-generated commit messages | Ollama | `plugins/git/` |
| Docker container lifecycle (build, run, stop, logs) | Docker SDK | `plugins/docker/` |

---

## Phase 4 — Vision (Screen Understanding) ⏳

**Status:** Planned · **ETA:** Week 16

AURA sees your screen. Local OCR and vision models for screen understanding.

| Deliverable | Tech | Module |
|---|---|---|
| Screen capture + OCR | Tesseract / LLaVA | `plugins/vision/` |
| Visual reasoning queries | LLaVA (local) | `plugins/vision/` |
| Screenshot-to-action pipeline | — | `plugins/vision/` |

---

## Phase 5 — GUI Dashboard ⏳

**Status:** Planned · **ETA:** Week 18

A desktop interface that makes AURA visual.

| Deliverable | Tech | Module |
|---|---|---|
| Main dashboard window | PyQt6 | `aura/gui/` |
| Live command log panel | PyQt6 | `aura/gui/` |
| System health widget | PyQt6 | `aura/gui/` |
| Voice input toggle + waveform display | PyQt6 | `aura/gui/` |

---

## Phase 6 — Memory + RAG ⏳

**Status:** Planned · **ETA:** Week 20

AURA remembers. Persistent semantic context across sessions.

| Deliverable | Tech | Module |
|---|---|---|
| ChromaDB vector store setup | ChromaDB | `plugins/memory/` |
| Project context indexer | nomic-embed-text | `plugins/memory/` |
| Semantic codebase search | ChromaDB | `plugins/memory/` |
| Conversation history persistence | ChromaDB | `plugins/memory/` |

---

## Phase 7 — Browser Automation ⏳

**Status:** Planned · **ETA:** Week 22

Sandboxed web automation for research, forms, and bookmarks.

| Deliverable | Tech | Module |
|---|---|---|
| Headless browser control | Playwright | `plugins/browser/` |
| Web research pipeline | Playwright + LLM | `plugins/browser/` |
| Form filling and bookmarks | Playwright | `plugins/browser/` |

---

## Phase 8 — Integrations ⏳

**Status:** Planned · **ETA:** Week 24

Optional bridges to services you trust — entirely opt-in, never default.

| Deliverable | Tech | Module |
|---|---|---|
| Spotify playback control | Spotify local API | `plugins/spotify/` |
| Weather data provider | Local weather API | `plugins/weather/` |
| Calendar management | CalDAV / local | `plugins/calendar/` |
| Email integration | IMAP / local | `plugins/gmail/` |

> Phase 2 is the currently active phase. Its code lands inside the existing `aura/` / `plugins/` tree; Phases 3–8 follow the same pattern. Final per-file layout is locked in when each phase opens — see [`docs/phases.md`](docs/phases.md) for the current plan.

---

## v1.0 Release

When all 8 phases ship, AURA v1.0 is:

- A fully offline, voice-controlled AI developer assistant
- Powered by local LLMs (no cloud, no API keys)
- With Git/Docker automation, screen vision, a desktop GUI, persistent memory, browser automation, and service integrations
- 100% open source under MIT

---

*See [CHANGELOG.md](CHANGELOG.md) for version history.*
