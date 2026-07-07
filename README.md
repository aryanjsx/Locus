<div align="center">

<img src="docs/assets/AURA.jpg" alt="Kommy — AURA voice assistant" width="800"/>

# Kommy

### Local voice assistant · powered by AURA

**AURA** — Autonomous Unified Response Architecture — is the offline, layered system underneath. **Kommy** is the persona you talk to ("Hey Kommy").

**Not another chatbot.** Kommy lives on your machine, executes real actions through sandboxed executors, and never phones home.

![Build Status](https://github.com/aryanjsx/AURA/actions/workflows/ci.yml/badge.svg)
![License](https://img.shields.io/github/license/aryanjsx/AURA)
![Stars](https://img.shields.io/github/stars/aryanjsx/AURA?style=social)
![Issues](https://img.shields.io/github/issues/aryanjsx/AURA)
![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)

**No cloud. No API keys. No subscriptions. No data leaving your machine. Ever.**

**Topics:** `python` · `open-source` · `voice-assistant` · `kommy` · `local-llm` · `ollama` · `whisper` · `offline-ai` · `automation` · `developer-tools` · `piper-tts` · `chromadb` · `pyqt6` · `gitpython` · `docker-sdk` · `ai`

[Get Started](#-getting-started) · [What It Can Do](#-what-kommy-can-do) · [Architecture](#-architecture) · [Roadmap](#-roadmap) · [Contribute](#-contributing)

</div>

> **Credibility status (2026-07-08):** Phase 2 adversarial audit — 20/20 violations verified fixed (Fix 13 + independent gap-closure pass: dead-reference sweep, per-action SafetyGate traces, per-intent LLM/TTS traces). **629 tests passing.** Live end-to-end voice demo recording is tracked separately (see [Known gaps](#known-gaps)).

---

## The Problem

Every "AI assistant" today is a chat window connected to someone else's server.

You type. It responds. That's it.

You can't tell it to **create a file on your desktop**. You can't ask it to **kill a runaway process**. You can't say **"open Chrome"** and have it happen. You can't speak a command and hear the answer.

ChatGPT can't touch your filesystem. Copilot can't monitor your CPU. AutoGPT burns through API credits and still can't move a file.

**AURA doesn't chat about doing things. It does them.**

---

## What Makes AURA Different

| | ChatGPT / Copilot | AutoGPT / AgentGPT | **AURA** |
|---|---|---|---|
| Runs locally | Cloud-only | Needs API keys | **Fully offline with Ollama** |
| Executes system actions | Chat only | Unreliable | **File, process, shell, voice** |
| Voice interface | No | No | **Whisper STT + TTS pipeline** |
| Privacy | Data sent to servers | Data sent to servers | **Nothing leaves your machine** |
| Security model | N/A | None | **Sandboxed, audited, policy-enforced** |
| Cost | $20/mo+ | API credits | **Free forever** |
| Works offline | No | No | **100% offline capable** |

---

## What Kommy Can Do

### Phase 1 — System Control (CLI)

```
> create file desktop/notes.txt
File created: C:\Users\You\Desktop\notes.txt

> cpu
CPU: 23.4%

> kill process chrome
Process 'chrome' terminated.

> create project desktop/my-app
Project 'my-app' created with src/ tests/ README.md .gitignore requirements.txt

> run command git status
```

### Phase 2 — Voice + Intelligence (verified in tests)

Kommy hears you, classifies intent, runs commands through SafetyGate when destructive, streams LLM responses to TTS, and speaks back — all locally.

**Verified pipeline paths** (unit/integration tests, 2026-07-08):

| Utterance | Intent | SafetyGate | Output path |
|---|---|---|---|
| "What is Python?" | `GENERAL_KNOWLEDGE` | — | `llm_stream` → TTS |
| "Write a function to sort a list" | `CODE_GENERATION` | — | `llm_stream` → TTS |
| "Push my code to GitHub" | `DEV_TASK` | — | `ShellExecutor` → `tts.speak(output)` |
| "What routes does my project have?" | `PROJECT_CONTEXT` | — | RAG hook → `llm_stream` → TTS |
| "What's the latest Node.js version?" (online) | `REALTIME_QUERY` | — | `BrowserExecutor.search` → TTS |
| "What's the latest Node.js version?" (offline) | `REALTIME_QUERY` | — | `llm_stream` + staleness warning → TTS |
| "Shutdown the computer" | `SYSTEM_COMMAND` | **Yes** (8s timeout) | Cancelled without confirm |
| "Restart the computer" | `SYSTEM_COMMAND` | **Yes** | Cancelled without confirm |
| "Log off the computer" | `SYSTEM_COMMAND` | **Yes** | Cancelled without confirm |
| "Close Chrome" | `SYSTEM_COMMAND` | **Yes** | Cancelled without confirm |
| "Open Chrome" | `SYSTEM_COMMAND` | — | Executor dispatch |

**Voice pipeline flow:**

```
Wake ("Hey Kommy") → STT → IntentRouter (regex → LLM fallback) → BrainController → SafetyGate (if destructive) → Execute / RAG augment / Stream LLM / Browser search → TTS
```

**Destructive commands always confirm** (shutdown, restart, log off, close app, kill process, shell, git push, docker remove — see `DESTRUCTIVE_ACTIONS` in `aura/schemas/command.py`):

| Voice Command | What Happens |
|---|---|
| "Create a folder named X on desktop" | Creates the folder instantly |
| "Delete file X from documents" | Deletes the file |
| "Open Chrome / Notepad / any app" | Launches the application |
| "Kill process chrome" | Asks for voice confirmation → terminates |
| "CPU" / "RAM" | Speaks current system usage |
| "Shutdown" / "Restart" | Asks for confirmation → executes |

**Intent classification** uses a two-tier router (`aura/core/intent_router.py`):

1. **Fast regex** for obvious patterns (system commands, dev tasks, knowledge questions) — no LLM call
2. **LLM fallback** for ambiguous input — 10s timeout, 3 retries, then `UNKNOWN`

| Intent | Routed To | Example |
|---|---|---|
| `SYSTEM_COMMAND` | SystemExecutor / SystemMonitor | "Open Chrome", "Shutdown", "CPU" |
| `CODE_GENERATION` | LLM stream (deepseek-coder) | "Write a REST endpoint in FastAPI" |
| `GENERAL_KNOWLEDGE` | LLM stream (mistral) | "Explain Docker networking" |
| `DEV_TASK` | ShellExecutor (allowlisted git/npm/docker) | "Push my code to GitHub" |
| `VISION_TASK` | Vision executor (Phase 4) | "What's on my screen?" |
| `PROJECT_CONTEXT` | RAG hook → LLM stream | "What routes does my project have?" |
| `REALTIME_QUERY` | Browser search (online) or LLM + staleness warning (offline) | "What's the latest Node.js version?" |
| `DEACTIVATE_SESSION` | Session controller | "Go to sleep", "That's all" |
| `UNKNOWN` | RAG hook → LLM stream | Unrecognized input after LLM retries |

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    INPUT LAYER                       │
│    CLI · "Hey Kommy" (Whisper) · CTRL+SPACE         │
├─────────────────────────────────────────────────────┤
│               VOICE PIPELINE (Phase 2)              │
│  VAD → Wake Word → Whisper STT → Intent Router     │
├─────────────────────────────────────────────────────┤
│                 REASONING LAYER                      │
│   OllamaClient (6 local models) · Intent Classifier │
├─────────────────────────────────────────────────────┤
│                 SAFETY LAYER                         │
│  SafetyGate · Voice Confirmation · Audit Chain      │
├─────────────────────────────────────────────────────┤
│                EXECUTION LAYER                       │
│  SystemExecutor · ShellExecutor · BrowserExecutor   │
│  SystemMonitor · CommandPlan → Dispatch → Result    │
├─────────────────────────────────────────────────────┤
│                  PLUGIN LAYER                        │
│  System · Git · Docker · Browser · Gmail · Spotify   │
│  Vision · Weather · Calendar · Memory                │
├─────────────────────────────────────────────────────┤
│                  OUTPUT LAYER                        │
│       Console · TTS (Edge/Piper/pyttsx3) · EventBus │
└─────────────────────────────────────────────────────┘
```

**Wake word detection (three-tier fallback):**

| Tier | Engine | How it works |
|---|---|---|
| **1 (default)** | Whisper keyword spotting | VAD detects speech → records 1.5s → Whisper transcribes → matches "Hey Kommy" + extracts command |
| **2** | openwakeword | Lightweight ONNX model (auto-fallback if Whisper unavailable) |
| **3** | CTRL+SPACE | Keyboard hotkey — always works alongside any voice tier |

**Performance characteristics (measured in tests, not marketing claims):**
- **Single-shot wake + command** — "Hey Kommy, what is Python?" captured in one recording when wake tier extracts inline command
- **Regex fast-path** — common intents classified without LLM round-trip
- **Streaming LLM responses** — `_stream_to_tts()` sends sentence chunks to TTS as tokens arrive
- **Model pre-warming** — primary model loaded at startup when Ollama is reachable
- **System commands** — executor-backed intents bypass LLM when BrainController resolves a concrete action

**Key design decisions:**
- The main process **never imports plugin code** — plugins run in isolated worker subprocesses over JSON IPC
- **SafetyGate** (`aura/security/safety_gate.py`) enforces voice confirmation for destructive ops with 8s timeout-based denial
- **EventBus** connects all modules via typed events — no direct coupling
- **ModeMonitor** detects online/offline — switches TTS engines and routes `REALTIME_QUERY` (browser search vs offline LLM)
- **RAG hook** (`aura/memory/context_retriever.py`) augments `PROJECT_CONTEXT` / `UNKNOWN` prompts when ChromaDB has stored context
- **TTS failover chain:** Edge TTS (online) → Piper (offline) → pyttsx3 (fallback)
- **Wake word shares the Whisper model** with STT — zero additional memory cost
- **Typed schemas** — `IntentObject`, `CommandPlan`, `ExecutionResult` enforce contracts between layers
- All config is centralized in `config.yaml` — no hardcoded values in source

---

## Getting Started

### Prerequisites

- **Python 3.10+**
- **Ollama** installed and running ([ollama.com](https://ollama.com))

### Install

```bash
git clone https://github.com/aryanjsx/AURA.git
cd AURA
pip install -r requirements.txt
```

### Pull the Ollama models

```bash
ollama pull llama3.2:3b-q4_0          # Fast voice responses
ollama pull mistral:7b-instruct-q4_0  # General reasoning (primary)
ollama pull llama3:8b-q4_0            # Complex reasoning fallback
ollama pull deepseek-coder:7b-q4_0    # Code generation
ollama pull llava:7b                  # Vision (Phase 4)
ollama pull nomic-embed-text          # Embeddings (Phase 6)
```

If your models are stored in a custom location (e.g., `D:\ollama\models`):

```powershell
$env:OLLAMA_MODELS="D:\ollama\models"
```

### Run

```bash
# Phase 2 — Voice pipeline (full experience)
python main.py

# Phase 1 — CLI mode (text commands only)
python -m aura
python -m aura --yes "cpu"
```

Say **"Hey Kommy"** to activate voice input, or press **CTRL+SPACE** as a manual fallback. Speak your command and AURA responds.

### Quick Reference

**Voice commands** (say "Hey Kommy" then speak naturally):

| Category | Voice Examples |
|---|---|
| **Files** | "Create a folder named project on desktop", "Delete file notes.txt from documents" |
| **Apps** | "Open Chrome", "Open Notepad", "Launch VS Code" |
| **System** | "CPU", "RAM", "Kill process chrome" |
| **Questions** | "What is Python?", "Explain Docker networking" |
| **Code** | "Write a function to sort a list" |

**CLI commands** (via `python -m aura`):

| Category | Commands |
|---|---|
| **Files** | `create file`, `delete file`, `rename file`, `move file`, `search files` |
| **System** | `cpu`, `ram`, `list processes`, `check system health`, `kill process` |
| **Projects** | `create project <path>` |
| **Shell** | `run command <cmd>` (allowlisted: git, npm, docker) |
| **npm** | `npm install [path]`, `npm run <script>` |

---

## Project Structure

```
AURA/
├── aura/
│   ├── core/
│   │   ├── config_loader.py    # YAML config with strict validation
│   │   ├── ollama_client.py    # Ollama API client with streaming
│   │   ├── intent_router.py    # Two-tier intent classification (regex + LLM)
│   │   ├── llm_brain.py        # Plan builder + model selector (not LLM caller)
│   │   ├── command_engine.py   # Intent → CommandPlan → Executor dispatch
│   │   ├── session_controller.py # Session lifecycle (active/sleep/wake)
│   │   ├── event_bus.py        # Singleton pub/sub event system
│   │   ├── errors.py           # Custom exception hierarchy
│   │   └── ...
│   ├── security/
│   │   ├── safety_gate.py      # Voice + CLI confirmation, audit logging
│   │   └── ...                 # Sandbox, audit, policy enforcement
│   ├── executors/
│   │   ├── system_executor.py  # OS-level: open/close apps, volume, shutdown
│   │   ├── shell_executor.py   # Allowlisted shell commands (git, npm, docker)
│   │   ├── browser_executor.py # HTTPS live search for REALTIME_QUERY (online)
│   │   └── system_monitor.py   # CPU, RAM, battery, disk, processes
│   ├── memory/
│   │   └── context_retriever.py # RAG retrieval hook (ChromaDB when available)
│   ├── schemas/
│   │   ├── intent.py           # IntentObject, IntentType enum (canonical)
│   │   └── command.py          # CommandPlan, ExecutionResult, DESTRUCTIVE_ACTIONS
│   ├── modules/
│   │   ├── stt.py              # Whisper speech-to-text engine
│   │   ├── tts.py              # Multi-engine text-to-speech
│   │   └── wake_word.py        # Whisper wake word + CTRL+SPACE fallback
│   ├── utils/
│   │   ├── mic_lock.py         # Shared mic mutex (wake word vs SafetyGate STT)
│   │   ├── app_registry.py     # Application name → executable resolution
│   │   └── mode_monitor.py     # Online/offline detection daemon
│   └── runtime/                # CLI execution engine, planner, worker IPC
├── docs/
│   ├── decisions/naming.md     # Kommy vs AURA naming ADR
│   └── assets/                 # README / site media
├── AURA_ENGINEERING_SPEC.md    # Phase 2 engineering contract
├── CHANGELOG.md
├── plugins/
│   ├── system/                 # File, process, shell operations
│   ├── git/                    # Git automation
│   ├── docker/                 # Docker lifecycle management
│   ├── browser/                # Web automation (Playwright)
│   ├── vision/                 # Screen capture + LLaVA
│   ├── gmail/                  # Email integration
│   ├── spotify/                # Music control
│   ├── calendar/               # Calendar events
│   ├── weather/                # Weather queries
│   └── memory/                 # ChromaDB semantic memory
├── tests/
│   ├── test_phase2_audit_part1.py  # EventBus, ModeMonitor, Ollama, Router
│   ├── test_phase2_audit_part2.py  # STT, WakeWord, TTS, Config, Safety
│   ├── test_destructive_gate.py    # DESTRUCTIVE_ACTIONS → SafetyGate (all pairs)
│   ├── test_violation2_closure.py  # RAG hook + REALTIME online/offline routing
│   ├── test_voice_destructive_path.py  # Voice utterances → SafetyGate
│   ├── test_safety_gate.py         # Confirmation tokens, timeout, audit
│   ├── test_system_executor.py     # SystemExecutor, ShellExecutor
│   └── fixtures/               # Test audio files, bad config
├── scripts/
│   ├── fix13_verify.py         # Fix 13 — 20-violation verification pass
│   └── phase2_integration_test.py
├── config.example.yaml         # Tracked template (copy to config.yaml)
├── main.py                     # Phase 2 voice pipeline entry point
└── requirements.txt
```

---

## Test Suite

**629 tests passing** (4 skipped) as of 2026-07-08. Run: `python -m pytest tests/ -q`

| Section | Tests | Status | Audit contract covered |
|---|---|---|---|
| EventBus (happy + adversarial) | 14 | Pass | Thread safety, handler isolation |
| ModeMonitor (happy + adversarial) | 7 | Pass | ONLINE/OFFLINE transitions |
| OllamaClient (happy + adversarial) | 8 | Pass | Retry count, unavailable error |
| IntentRouter + IntentObject | 13 | Pass | Schema fields, two-tier classify |
| STTEngine (happy + adversarial) | 13 | Pass | Never raises, concurrent isolation |
| WakeWordListener (happy + adversarial) | 11 | Pass | Non-blocking start, mic errors |
| TTSEngine (happy + adversarial) | 9 | Pass | Queue, interrupt, fallback chain |
| SystemExecutor + ShellExecutor | 27 | Pass | Actions, shell allowlist |
| SafetyGate | 14 | Pass | Tokens, timeout, audit on CLI path |
| Destructive gate (all DESTRUCTIVE_ACTIONS) | parametric | Pass | Re-derives `is_destructive` |
| Violation #2 closure (RAG + REALTIME) | 9 | Pass | RAG flags, browser/offline branches |
| Voice destructive path | 4 | Pass | shutdown/restart/log_off/close_app utterances |
| SessionController | 12 | Pass | Lifecycle, inactivity, mic pause |
| Config validation | 12 | Pass | Required keys, env overrides, `config.example.yaml` |
| Safety (static analysis) | 5 | Pass | No shell=True, eval/exec, subprocess f-strings |
| Regression guards | 5 | Pass | Singleton bus, layer boundaries |

**Security verified (static + unit tests):** `shell=True` = 0 and `eval(`/`exec(` = 0 in `aura/` production code; subprocess uses list form; STTEngine does not write recordings to disk; schema consolidation confirmed. Layer-boundary enforcement is tested via import guards in `test_phase2_audit_part2.py`.

---

## Known gaps

| Gap | Status |
|---|---|
| Live wake-word → audible TTS screen recording for README | Not yet recorded — requires manual capture session |
| ChromaDB memory population (RAG returns context) | Hook implemented; install `chromadb` + index project docs for live retrieval |
| GitHub Pages demo embed | Pending live demo clip |
| macOS/Linux wake-word CI matrix | Tracked in [#2](https://github.com/aryanjsx/AURA/issues/2) |
| Voice-path SafetyGate audit-log assertions | Tracked in [#3](https://github.com/aryanjsx/AURA/issues/3) |

---

## Roadmap

| Phase | What Ships | Status |
|---|---|---|
| **Phase 0 — Core Infrastructure** | Event bus, config, registry, CLI, execution backbone | Done |
| **Phase 1 — System Plugin** | File/process/npm operations, sandbox, permissions, audit chain | Done |
| **Phase 2 — Voice + Intelligence** | Whisper STT, Ollama LLM routing, TTS, intent classification, executors, safety gate, RAG hook, realtime browser search | Done — 20/20 audit violations verified (2026-07-08) |
| **Phase 3 — Dev Tools** | Git automation, Docker lifecycle, browser automation | Next |
| **Phase 4 — Vision** | Screen capture, OCR, visual reasoning with LLaVA | Planned |
| **Phase 5 — GUI Dashboard** | PyQt6 desktop interface with live command log | Planned |
| **Phase 6 — Memory + RAG** | ChromaDB indexing, conversation history, memory event subscribers | Partial — retrieval hook + config scaffold in Phase 2 |
| **Phase 7 — Browser Automation** | Sandboxed web research with Playwright | Partial — DuckDuckGo search for `REALTIME_QUERY` (online) |
| **Phase 8 — Integrations** | Spotify, Weather, Calendar, Gmail bridges | Planned |

---

## Philosophy

> **"If it needs the internet to think, it's not your AI."**

1. **Local-first** — No cloud dependency. No API keys. Works on airplane mode.
2. **Actions over answers** — AURA doesn't explain how to create a file. It creates the file.
3. **Security is non-negotiable** — Sandboxed execution, tamper-evident audit logs, hash-chained integrity.
4. **Modular by design** — Every capability is a plugin. Add what you need. Remove what you don't.
5. **Developer-owned** — Open source. No telemetry. No tracking. Your machine, your rules.

---

## Contributing

Kommy is open source under MIT. If any of the [GitHub topics](https://github.com/aryanjsx/AURA/topics) above match your stack, there is a concrete place to contribute — no prior AURA experience required.

**Quick start:** [Fork](https://github.com/aryanjsx/AURA/fork) → clone → branch → PR. Stars help others find the project on GitHub Explore and in topic searches (`voice-assistant`, `local-llm`, `ollama`, etc.).

| If you know… | Start here | Example contribution |
|---|---|---|
| `python` / `open-source` | `tests/`, `docs/`, issue triage | Fix a test, improve CONTRIBUTING |
| `whisper` / `voice-assistant` | `aura/modules/stt.py`, `wake_word.py` | Wake-word accuracy, mic handling ([#2](https://github.com/aryanjsx/AURA/issues/2)) |
| `ollama` / `local-llm` / `ai` | `aura/core/ollama_client.py`, `intent_router.py` | Prompt tuning, router edge cases |
| `piper-tts` / offline TTS | `aura/modules/tts.py` | Engine fallback, temp-file cleanup |
| `automation` / `developer-tools` | `aura/executors/`, `plugins/system/` | New system commands, executor tests |
| `gitpython` / `docker-sdk` | `plugins/git/`, `plugins/docker/` | Phase 3 plugin stubs → working commands |
| `chromadb` | `aura/memory/`, `plugins/memory/` | Index project docs, memory event subscribers (Phase 6) |
| `pyqt6` | `aura/gui/` (planned) | Phase 5 dashboard mockups |

1. Fork the repo
2. Create your branch (`git checkout -b feat/amazing-feature`)
3. Commit with [Conventional Commits](https://www.conventionalcommits.org/) (`feat(core): add amazing feature`)
4. Push and open a Pull Request

See [CONTRIBUTING.md](CONTRIBUTING.md) for full guidelines. Starter issues: `good first issue` ([#2](https://github.com/aryanjsx/AURA/issues/2)) · `help wanted` ([#3](https://github.com/aryanjsx/AURA/issues/3)).

**Active areas where we need help:**
- Plugin development (Git, Docker, Browser, Gmail, Spotify)
- Ollama prompt engineering for developer tasks
- Cross-platform testing (macOS, Linux)
- Test coverage expansion
- GUI dashboard design (Phase 5)

---

## Star & fork

If Kommy's vision resonates — local AI that **executes real actions** and **respects your privacy** — a star or fork takes one second and helps this repo surface in GitHub topic feeds for `offline-ai`, `voice-assistant`, and `local-llm`.

[![Star this repo](https://img.shields.io/github/stars/aryanjsx/AURA?style=for-the-badge&logo=github&label=Star%20Kommy&color=yellow)](https://github.com/aryanjsx/AURA/stargazers)
[![Fork this repo](https://img.shields.io/github/forks/aryanjsx/AURA?style=for-the-badge&logo=github&label=Fork&color=555)](https://github.com/aryanjsx/AURA/fork)

---

<div align="center">

**Kommy** — local voice assistant · **AURA** — Autonomous Unified Response Architecture

Built offline. Powered locally. Yours completely.

See [CHANGELOG.md](CHANGELOG.md) · [Naming ADR](docs/decisions/naming.md)

[GitHub](https://github.com/aryanjsx/AURA) · [Issues](https://github.com/aryanjsx/AURA/issues) · [Contributing](CONTRIBUTING.md) · [Roadmap](ROADMAP.md)

MIT License — Built by [@aryanjsx](https://github.com/aryanjsx)

</div>
