<div align="center">

<img src="docs/assets/AURA.jpg" alt="AURA" width="800"/>

# AURA

### Your computer already has an AI. It just doesn't know it yet.

**Not another chatbot.** AURA is a system-level AI that lives on your machine, executes real actions, and never phones home.

![Build Status](https://github.com/aryanjsx/AURA/actions/workflows/ci.yml/badge.svg)
![License](https://img.shields.io/github/license/aryanjsx/AURA)
![Stars](https://img.shields.io/github/stars/aryanjsx/AURA?style=social)
![Issues](https://img.shields.io/github/issues/aryanjsx/AURA)
![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)

**No cloud. No API keys. No subscriptions. No data leaving your machine. Ever.**

[Get Started](#-getting-started) · [What It Can Do](#-what-aura-can-do) · [Architecture](#-architecture) · [Roadmap](#-roadmap) · [Contribute](#-contributing)

</div>

"Kommy" a phonetic evolution of "commy" (companion) turned into a proper name, distinct from the acronym pattern of AURA.
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

## What AURA Can Do

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

### Phase 2 — Voice + Intelligence (Current)

AURA now **hears you, thinks locally, speaks back, and executes real actions** — powered entirely by local models.

```
"Hey Jarvis, create a folder named project on desktop"
  → Wake word + command extracted in one step
  → Intent: SYSTEM_COMMAND (regex, 0ms)
  → Folder created instantly
  → TTS: "Folder project created on Desktop."

"Hey Jarvis, what is Python?"
  → Intent: GENERAL_KNOWLEDGE (regex, 0ms)
  → Streams response from llama3.2:1b
  → TTS speaks first sentence in ~3s

"Hey Jarvis, open Chrome"
  → Intent: SYSTEM_COMMAND
  → Chrome opens immediately
  → TTS: "Opening Chrome."

[CTRL+SPACE] → "Write a Python function to sort a list"
  → Intent: CODE_GENERATION
  → Streams from deepseek-coder:6.7b
```

**Voice pipeline flow:**

```
Wake ("Hey Jarvis") → Command Extraction → Regex Intent (0ms) → Execute / Stream LLM → TTS
```

**System commands execute directly — no LLM round-trip:**

| Voice Command | What Happens |
|---|---|
| "Create a folder named X on desktop" | Creates the folder instantly |
| "Delete file X from documents" | Deletes the file |
| "Open Chrome / Notepad / any app" | Launches the application |
| "Kill process chrome" | Terminates the process |
| "CPU" / "RAM" | Speaks current system usage |

**7 intent types** classified instantly via regex (no LLM call):

| Intent | Routed To | Example |
|---|---|---|
| `SYSTEM_COMMAND` | Direct execution | "Create folder", "Open Chrome", "Kill process" |
| `CODE_GENERATION` | deepseek-coder:6.7b | "Write a REST endpoint in FastAPI" |
| `GENERAL_KNOWLEDGE` | llama3.2:1b (streamed) | "Explain Docker networking" |
| `DEV_TASK` | llama3.2:1b | "Push my code to GitHub" |
| `VISION_TASK` | llava:7b | "What's on my screen?" |
| `PROJECT_CONTEXT` | llama3.2:1b | "What routes does my project have?" |
| `REALTIME_QUERY` | llama3.2:1b | "What's the latest Node.js version?" |

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    INPUT LAYER                       │
│    CLI · "Hey Jarvis" (Whisper) · CTRL+SPACE        │
├─────────────────────────────────────────────────────┤
│               VOICE PIPELINE (Phase 2)              │
│  VAD → Wake Word → Whisper STT → Intent Router     │
├─────────────────────────────────────────────────────┤
│                 REASONING LAYER                      │
│   OllamaClient (6 local models) · Intent Classifier │
├─────────────────────────────────────────────────────┤
│                 SECURITY LAYER                       │
│    Sandbox · Policy · Permissions · Audit Chain      │
├─────────────────────────────────────────────────────┤
│                EXECUTION LAYER                       │
│   Isolated Worker Process · Plugin Registry · IPC    │
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
| **1 (default)** | Whisper keyword spotting | VAD detects speech → records 1.5s → Whisper transcribes → matches "Hey Jarvis" + extracts command |
| **2** | openwakeword | Lightweight ONNX model (auto-fallback if Whisper unavailable) |
| **3** | CTRL+SPACE | Keyboard hotkey — always works alongside any voice tier |

**Performance optimizations:**
- **Single-shot wake + command** — "Hey Jarvis, what is Python?" is captured in one recording, no second prompt
- **Regex-only intent classification** — 0ms classification, no LLM round-trip
- **Streaming LLM responses** — TTS speaks the first sentence while the model is still generating
- **Model pre-warming** — primary model is loaded into RAM at startup for instant inference
- **System commands bypass LLM entirely** — file/folder/app operations execute directly

**Key design decisions:**
- The main process **never imports plugin code** — plugins run in isolated worker subprocesses over JSON IPC
- **EventBus** connects all modules via 18 typed events — no direct coupling
- **ModeMonitor** detects online/offline and switches TTS engines automatically
- **TTS failover chain:** Edge TTS (online) → Piper (offline) → pyttsx3 (fallback)
- **Wake word shares the Whisper model** with STT — zero additional memory cost
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
ollama pull llama3.2:1b            # Primary (fast voice responses)
ollama pull llama3.2:3b            # Reasoning fallback
ollama pull deepseek-coder:6.7b    # Code generation
ollama pull llava:7b               # Vision (Phase 4)
ollama pull nomic-embed-text:latest # Embeddings (Phase 6)
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

Say **"Hey Jarvis"** to activate voice input, or press **CTRL+SPACE** as a manual fallback. Speak your command and AURA responds.

### Quick Reference

**Voice commands** (say "Hey Jarvis" then speak naturally):

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
│   │   ├── intent_router.py    # Regex-based intent classification
│   │   ├── voice_executor.py   # Direct system command execution
│   │   ├── errors.py           # Custom exception hierarchy
│   │   └── ...
│   ├── modules/
│   │   ├── stt.py              # Whisper speech-to-text engine
│   │   ├── tts.py              # Multi-engine text-to-speech
│   │   └── wake_word.py        # Whisper-based wake word + CTRL+SPACE
│   ├── utils/
│   │   ├── audio_input.py      # Microphone device resolution
│   │   ├── event_bus.py        # Singleton pub/sub with 18 event types
│   │   └── mode_monitor.py     # Online/offline detection daemon
│   ├── security/               # Sandbox, audit, policy enforcement
│   └── runtime/                # Execution engine, planner, worker IPC
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
│   └── fixtures/               # Test audio files, bad config
├── scripts/                    # Diagnostic and integration test scripts
├── config.yaml                 # Central configuration
├── main.py                     # Phase 2 voice pipeline entry point
└── requirements.txt
```

---

## Test Suite

The adversarial audit suite covers every module with both happy-path and edge-case tests:

| Section | Tests | Status |
|---|---|---|
| EventBus (happy + adversarial) | 14 | All pass |
| ModeMonitor (happy + adversarial) | 7 | All pass |
| OllamaClient (happy + adversarial) | 8 | All pass |
| IntentRouter + IntentObject | 13 | All pass |
| STTEngine (happy + adversarial) | 13 | All pass |
| WakeWordListener (happy + adversarial) | 11 | All pass |
| TTSEngine (happy + adversarial) | 9 | All pass |
| Config validation | 2 | All pass |
| Safety (static analysis) | 5 | All pass |
| Pipeline E2E | 1 | All pass |
| Regression guards | 5 | All pass |

**Security verified:** No `shell=True`, no `eval`/`exec`, no subprocess string injection, no audio persisted to disk, all layer boundaries enforced.

---

## Roadmap

| Phase | What Ships | Status |
|---|---|---|
| **Phase 0 — Core Infrastructure** | Event bus, config, registry, CLI, execution backbone | Done |
| **Phase 1 — System Plugin** | File/process/npm operations, sandbox, permissions, audit chain | Done |
| **Phase 2 — Voice + Intelligence** | Whisper STT, Ollama LLM routing, TTS, intent classification | Done |
| **Phase 3 — Dev Tools** | Git automation, Docker lifecycle, browser automation | Next |
| **Phase 4 — Vision** | Screen capture, OCR, visual reasoning with LLaVA | Planned |
| **Phase 5 — GUI Dashboard** | PyQt6 desktop interface with live command log | Planned |
| **Phase 6 — Memory + RAG** | ChromaDB semantic memory, conversation history | Planned |
| **Phase 7 — Browser Automation** | Sandboxed web research with Playwright | Planned |
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

We're building something big and we want you in.

1. Fork the repo
2. Create your branch (`git checkout -b feat/amazing-feature`)
3. Commit with [Conventional Commits](https://www.conventionalcommits.org/) (`feat(core): add amazing feature`)
4. Push and open a Pull Request

See [CONTRIBUTING.md](CONTRIBUTING.md) for full guidelines. Check out [open issues](https://github.com/aryanjsx/AURA/issues) — look for `good first issue` and `help wanted`.

**Active areas where we need help:**
- Plugin development (Git, Docker, Browser, Gmail, Spotify)
- Ollama prompt engineering for developer tasks
- Cross-platform testing (macOS, Linux)
- Test coverage expansion
- GUI dashboard design (Phase 5)

---

## Star This Repo

If AURA's vision resonates with you — an AI that **runs locally**, **executes real actions**, and **respects your privacy** — drop a star.

It takes one second and tells us you believe AI should be **owned, not rented**.

[![Star this repo](https://img.shields.io/github/stars/aryanjsx/AURA?style=for-the-badge&logo=github&label=Star%20AURA&color=yellow)](https://github.com/aryanjsx/AURA)

---

<div align="center">

**AURA — Autonomous Unified Response Architecture**

Built offline. Powered locally. Yours completely.

[GitHub](https://github.com/aryanjsx/AURA) · [Issues](https://github.com/aryanjsx/AURA/issues) · [Contributing](CONTRIBUTING.md) · [Roadmap](ROADMAP.md)

MIT License — Built by [@aryanjsx](https://github.com/aryanjsx)

</div>
