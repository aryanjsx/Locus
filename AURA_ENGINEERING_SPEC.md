**Autonomous Unified Response Architecture**
Version 1.0 · Pre-Phase 2 Engineering Document · Author: Aryan Kumar (aryanjsx)

---

## Table of Contents

1. System Architecture
2. Module Contracts
3. Intent Schema
4. Command Schema
5. Safety Rules
6. Event Pipeline
7. Appendix — Phase 2 Pre-Flight Checklist

---

## 1. System Architecture

### 1.1 High-Level Overview

AURA is a layered, modular system where every user interaction passes through exactly 8 pipeline stages in order. Each stage is independently replaceable — swapping a TTS engine, for example, requires zero changes to any other module.

**Core Design Principles:**

- Strict layer separation — no module calls across non-adjacent layers
- Single configuration source of truth — `config.yaml` drives all runtime behaviour
- Fail-safe defaults — every layer degrades gracefully to a simpler fallback
- Event-driven inter-module communication via a central event bus
- Zero global state — all state is passed explicitly through the pipeline

---

### 1.2 The 8-Layer Pipeline

| Layer | Module | File | Input → Output |
| --- | --- | --- | --- |
| 01 — Wake Word | `WakeWordListener` | `modules/wake_word.py` | Audio stream → wake event |
| 02 — Speech-to-Text | `STTEngine` | `modules/stt.py` | Audio buffer → text string |
| 03 — Intent Classify | `IntentRouter` | `core/router.py` | Text → IntentObject |
| 04 — Intelligence | `BrainController` | `core/llm_brain.py` | IntentObject → CommandPlan |
| 05 — LLM Processing | `OllamaClient` | `core/llm_brain.py` | Prompt → LLM response JSON |
| 06 — Command Engine | `CommandEngine` | `core/command_engine.py` | CommandPlan → ExecutorRef |
| 07 — Execution | `ExecutorDispatch` | `executors/*.py` | ExecutorRef → ActionResult |
| 08 — TTS Response | `TTSEngine` | `modules/tts.py` | ActionResult → spoken audio |

---

### 1.3 Module Dependency Graph

```
main.py
  └── core/config.py              (loads config.yaml — must be first)
  └── core/router.py              (depends on: config, ollama_client)
  └── core/command_engine.py      (depends on: router, all executors)
  └── core/llm_brain.py           (depends on: config, ollama, memory)
  └── modules/wake_word.py        (depends on: config)
  └── modules/stt.py              (depends on: config)
  └── modules/tts.py              (depends on: config, mode_monitor)
  └── modules/vision.py           (depends on: ollama, pillow, tesseract)
  └── memory/memory_manager.py    (depends on: chroma_store, embeddings)
  └── utils/mode_monitor.py       (standalone — no other AURA dependencies)
```

---

### 1.4 Startup Sequence

The following order is **mandatory**. Any deviation causes import errors or missing config:

1. Load `config.yaml` → validate required keys → exit with clear error if missing
2. Start connectivity monitor in daemon thread
3. Pre-load Whisper model into RAM (prevents cold-start delay on first command)
4. Start Ollama health check — confirm service is running at `localhost:11434`
5. Verify all configured models are pulled (`ollama list`) — warn if missing
6. Initialize ChromaDB persistent collection *(Phase 6+)*
7. Start wake word listener in daemon thread
8. Launch PyQt6 GUI event loop on main thread *(Phase 5+)*

---

## 2. Module Contracts

Every AURA module is defined by a strict interface contract. Modules communicate only through these contracts — never by importing internal helpers from each other.

---

### 2.1 WakeWordListener

| Property | Value |
| --- | --- |
| Class | `WakeWordListener` |
| File | `aura/modules/wake_word.py` |
| Depends on | `config.py`, PyAudio, pvporcupine |
| Thread | Daemon thread — runs continuously in background |
| Emits | `WAKE_WORD_DETECTED` event on event bus |
| Errors | Emits `WAKE_WORD_ERROR` if mic unavailable — does not crash |

```python
# Public interface
listener = WakeWordListener(config)
listener.start()   # non-blocking, spawns daemon thread
listener.stop()    # graceful shutdown
```

---

### 2.2 STTEngine

| Property | Value |
| --- | --- |
| Class | `STTEngine` |
| File | `aura/modules/stt.py` |
| Depends on | `config.py`, whisper, PyAudio, sounddevice |
| Model load | Pre-loaded at startup — **NOT** on first transcription call |
| Returns | `TranscriptionResult: { text: str, confidence: float, duration_ms: int }` |
| Errors | Returns empty `TranscriptionResult` on timeout — never raises to caller |

```python
# Public interface
stt = STTEngine(config)
stt.preload()                           # call at startup
result = stt.transcribe(audio_buffer)  # blocking, returns TranscriptionResult
```

---

### 2.3 IntentRouter

| Property | Value |
| --- | --- |
| Class | `IntentRouter` |
| File | `aura/core/router.py` |
| Depends on | `config.py`, `OllamaClient` |
| Input | `raw_text: str` |
| Returns | `IntentObject` (see Section 3) |
| Fallback | If LLM returns invalid JSON after 3 retries → `IntentType.UNKNOWN` |
| Timeout | 10 seconds per classification attempt |

---

### 2.4 OllamaClient

| Property | Value |
| --- | --- |
| Class | `OllamaClient` |
| File | `aura/core/ollama_client.py` |
| Depends on | `config.py`, httpx |
| Base URL | `http://localhost:11434` (configurable in config.yaml) |
| Returns | `OllamaResponse: { text: str, model: str, duration_ms: int }` |
| Errors | Raises `OllamaUnavailableError` if service not running — caught at `BrainController` |
| Retry | 3 attempts with 2s backoff for transient failures |

---

### 2.5 CommandEngine

| Property | Value |
| --- | --- |
| Class | `CommandEngine` |
| File | `aura/core/command_engine.py` |
| Depends on | All executor modules |
| Input | `CommandPlan` (see Section 4) |
| Returns | `ExecutionResult: { success: bool, output: str, error: str | None }` |
| Safety | **MUST** call `SafetyGate.check()` before executing any destructive command |

---

### 2.6 TTSEngine

| Property | Value |
| --- | --- |
| Class | `TTSEngine` |
| File | `aura/modules/tts.py` |
| Depends on | `config.py`, `mode_monitor`, piper-tts OR edge-tts OR pyttsx3 |
| Input | `text: str` |
| Behaviour | Reads `mode_monitor` to pick engine — automatic, transparent to caller |
| Queue | Internal audio queue — concurrent `speak()` calls queue, never overlap |
| Interrupt | `tts.interrupt()` stops current audio and clears queue |

---

## 3. Intent Schema

### 3.1 IntentObject — Full Definition

```python
class IntentObject:
    intent_type:    IntentType         # enum — see 3.2
    raw_text:       str                # original user utterance
    cleaned_text:   str                # lowercase, stripped
    entities:       dict[str, Any]     # extracted slots (filename, branch, etc.)
    model_override: str | None         # force a specific Ollama model
    requires_rag:   bool               # True if memory context needed
    confidence:     float              # 0.0–1.0 classification confidence
    timestamp:      datetime           # when classification occurred
```

---

### 3.2 IntentType Enum

| Intent | Trigger Examples | Model | RAG? | Confirms? |
| --- | --- | --- | --- | --- |
| `GENERAL_KNOWLEDGE` | "What is a closure?" / "Explain Kubernetes" | `mistral:7b` | Maybe | No |
| `CODE_GENERATION` | "Write a Python sort fn" / "Fix this function" | `deepseek-coder:7b` | No | No |
| `SYSTEM_COMMAND` | "Open Chrome" / "Take a screenshot" | `llama3.2:3b` | No | No |
| `DEV_TASK` | "Push to GitHub" / "Start Docker container" | `llama3.2:3b` | No | Yes* |
| `PROJECT_CONTEXT` | "What routes does my app have?" | `mistral:7b` | Always | No |
| `VISION_TASK` | "What's on my screen?" / "Read this error" | `llava:7b` | No | No |
| `REALTIME_QUERY` | "Latest Node.js version?" / "Current BTC price" | `mistral:7b` | No | No |
| `UNKNOWN` | Anything unclassified after 3 retries | `mistral:7b` | Always | No |

> * `DEV_TASK` confirmations only required when the action is destructive. See Section 5.
> 

---

### 3.3 Entity Extraction — Required Slots per Intent

| Intent | Required Entity Keys | Optional Entity Keys |
| --- | --- | --- |
| `SYSTEM_COMMAND` | `action` (open/close/minimize) | `app_name`, `window_title` |
| `DEV_TASK` | `task_type` (git/docker/npm) | `branch_name`, `container_name`, `repo_path` |
| `CODE_GENERATION` | `language` | `function_name`, `description`, `output_file` |
| `VISION_TASK` | `vision_mode` (describe/read/detect) | `region` (full/partial) |
| `FILE_OPERATION` | `operation` (create/move/delete) | `source_path`, `dest_path`, `filename` |

---

### 3.4 Intent Classification Prompt Contract

The router sends the following system prompt to Ollama. This prompt is the contract — **do not alter it without updating all downstream consumers.**

```
SYSTEM PROMPT (router_classify_v1):

You are AURA's intent classifier. Classify the user's command.

Return ONLY valid JSON. No explanation. No markdown. No preamble.

Schema:
{
  "intent_type": "<INTENT_TYPE>",
  "confidence": <float 0.0-1.0>,
  "entities": { "<key>": "<value>" },
  "requires_rag": <true|false>
}

Valid intent types: GENERAL_KNOWLEDGE, CODE_GENERATION, SYSTEM_COMMAND,
DEV_TASK, PROJECT_CONTEXT, VISION_TASK, REALTIME_QUERY, UNKNOWN
```

---

## 4. Command Schema

### 4.1 Command Plan — Full Definition

```python
class CommandPlan:
    executor:         ExecutorType       # which executor handles this
    action:           str                # specific action within executor
    params:           dict[str, Any]     # action parameters (validated)
    requires_confirm: bool               # must user confirm before exec?
    is_destructive:   bool               # triggers safety gate
    timeout_seconds:  int                # abort if execution exceeds this
    intent_ref:       IntentObject       # back-reference for context
```

---

### 4.2 ExecutorType → Action Map

| ExecutorType | Actions | Destructive Actions |
| --- | --- | --- |
| `SHELL` | `run_command`, `capture_output` | None |
| `FILE` | `create`, `read`, `list`, `move`, `rename`, `copy` | `delete`, `rmdir` |
| `SYSTEM` | `open_app`, `close_app`, `screenshot`, `get_stats`, `set_vol` | `kill_process` |
| `GIT` | `status`, `log`, `add`, `commit`, `push`, `pull`, `branch_list` | `branch_delete`, `force_push`, `reset_hard` |
| `DOCKER` | `list`, `start`, `stop`, `logs`, `inspect` | `build`, `remove`, `prune` |
| `NPM` | `start`, `install`, `build`, `test`, `run_script` | None |
| `BROWSER` | `navigate`, `search`, `extract_text`, `fill_form`, `click` | None |
| `VISION` | `describe_screen`, `read_text`, `detect_elements` | None |

---

### 4.3 ExecutionResult — Full Definition

```python
class ExecutionResult:
    success:       bool
    output:        str        # human-readable result for TTS
    data:          Any        # structured result for further processing
    error:         str | None # error message if success=False
    executor:      ExecutorType
    duration_ms:   int
    was_confirmed: bool       # True if user confirmed a destructive action
```

---

### 4.4 Intent → CommandPlan Routing Table

| Intent Type | Primary Executor | Routing Logic |
| --- | --- | --- |
| `SYSTEM_COMMAND` | `SYSTEM` / `SHELL` | `entity[action]` → SYSTEM; if shell syntax detected → SHELL |
| `DEV_TASK` (git) | `GIT` | `entity[task_type] == "git"` → GIT executor |
| `DEV_TASK` (docker) | `DOCKER` | `entity[task_type] == "docker"` → DOCKER executor |
| `DEV_TASK` (npm) | `NPM` | `entity[task_type] == "npm"` → NPM executor |
| `CODE_GENERATION` | `SHELL` + `FILE` | LLM generates code → `FILE.create` → `SHELL.open_in_editor` |
| `VISION_TASK` | `VISION` | Always VISION executor — no fallback |
| `REALTIME_QUERY` | `BROWSER` (if online) | Online: `BROWSER.search`; Offline: LLM with staleness warning |
| `GENERAL_KNOWLEDGE` | None (LLM only) | No executor — LLM response goes directly to TTS |
| `PROJECT_CONTEXT` | None (RAG + LLM only) | RAG retrieves context → LLM → TTS |
| `UNKNOWN` | None (RAG + LLM only) | RAG retrieves context → LLM → TTS |

---

## 5. Safety Rules

> ⚠️ **NON-NEGOTIABLE:** All rules in this section are hardcoded. They cannot be overridden by `config.yaml`, voice command, or any future feature. Any PR that weakens a safety rule will be rejected.
> 

---

### 5.1 Destructive Operations — Confirmation Gate

A command is classified as **DESTRUCTIVE** if it permanently modifies, deletes, or irreversibly alters data, files, processes, or repository state. All destructive commands **MUST** go through `SafetyGate` before execution.

### Destructive Command List (Complete)

| Category | Command | Confirmation Prompt (AURA speaks) |
| --- | --- | --- |
| File | Delete any file | *"I'm about to delete [filename]. Say yes to confirm."* |
| File | `rmdir` / delete folder | *"This will permanently delete the folder [name] and all contents. Confirm?"* |
| Git | `git push` | *"Pushing [N] commits to [branch] on [remote]. Confirm?"* |
| Git | `git reset --hard` | *"Hard reset will discard all uncommitted changes. This cannot be undone. Confirm?"* |
| Git | Branch delete | *"Deleting branch [name]. Confirm?"* |
| Git | Force push | *"Force push to [branch] will overwrite remote history. Are you absolutely sure?"* |
| Docker | Remove container | *"Removing container [name] and its data. Confirm?"* |
| Docker | `docker prune` | *"System prune removes all stopped containers and unused images. Confirm?"* |
| System | Kill process | *"Killing process [name] (PID [N]). Confirm?"* |

### SafetyGate Behaviour

- AURA speaks the confirmation prompt aloud via TTS
- AURA listens for exactly **8 seconds** for a spoken response
- Only `"yes"`, `"confirm"`, `"do it"`, or `"proceed"` are accepted as confirmation
- Any other response, silence, or timeout → command is **CANCELLED**
- AURA speaks *"Cancelled."* if not confirmed
- Confirmation decision and outcome are logged to `safety_audit.log`

---

### 5.2 Input Sanitization Rules

> 🚫 **CRITICAL:** User voice input **MUST NEVER** be passed directly to `subprocess`, shell, or any system call. All parameters must be validated against an allowlist before execution.
> 

| Rule | Description |
| --- | --- |
| No shell injection | All subprocess calls use list form: `subprocess.run(["git", "push"])` — **NEVER** `subprocess.run(f"git push {branch}", shell=True)` |
| Path validation | All file paths must resolve within the user's home directory — reject any path containing `..` or absolute paths outside home |
| Command allowlist | Only commands defined in `ExecutorType.actions` are executable — free-form shell commands from voice are blocked |
| Credential protection | Any text typed via PyAutoGUI (passwords, tokens) must **NEVER** be stored in memory, logs, or ChromaDB |
| No eval/exec | Python `eval()` and `exec()` are permanently banned — no exceptions |

---

### 5.3 Memory Safety Rules

- Never store audio recordings — only transcribed text
- Never store screen screenshots — only LLaVA text descriptions
- Never store anything the user types via PyAutoGUI keyboard control
- Conversation entries older than **90 days** are auto-purged from ChromaDB
- All memory operations must handle ChromaDB exceptions gracefully — a memory failure must never crash the main pipeline

---

### 5.4 Error Handling Contract

| Error Type | Handler | AURA Response | Pipeline Action |
| --- | --- | --- | --- |
| Ollama unavailable | `BrainController` | *"My thinking engine isn't responding. Please start Ollama."* | Suspend pipeline, retry every 30s |
| Whisper transcription fail | `STTEngine` | *"I didn't catch that. Could you repeat?"* | Return empty result, re-enter listen state |
| Executor error | `CommandEngine` | *"I ran into a problem: [brief error]. Check the logs."* | Log full error, return failure result |
| Wake word false positive | `WakeWordListener` | Silent — re-enter listen state | No TTS, no action |
| ChromaDB unavailable | `MemoryManager` | Silent — log warning | Continue without memory, do not crash |
| TTS engine failure | `TTSEngine` | Fallback to pyttsx3 silently | Log warning, continue on fallback |

---

## 6. Event Pipeline

### 6.1 Event Bus Architecture

AURA uses a central synchronous event bus for cross-module communication. Modules never import each other directly — they subscribe to and emit events. This ensures full decoupling and makes each module independently testable.

```python
# Event Bus — core/event_bus.py
class EventBus:
    def subscribe(self, event_type: EventType, handler: Callable) -> None
    def emit(self, event_type: EventType, payload: EventPayload) -> None
    def unsubscribe(self, event_type: EventType, handler: Callable) -> None
```

---

### 6.2 Full Event Catalog

| Event | Emitter | Subscribers | Payload |
| --- | --- | --- | --- |
| `WAKE_WORD_DETECTED` | `WakeWordListener` | `STTEngine`, GUI | `{ timestamp }` |
| `RECORDING_STARTED` | `STTEngine` | GUI, TTSEngine (mute self) | `{ timestamp }` |
| `RECORDING_STOPPED` | `STTEngine` | GUI | `{ duration_ms }` |
| `TRANSCRIPTION_COMPLETE` | `STTEngine` | `IntentRouter`, GUI, `MemoryMgr` | `{ text, confidence, duration_ms }` |
| `INTENT_CLASSIFIED` | `IntentRouter` | `BrainController`, GUI | `{ IntentObject }` |
| `LLM_REQUEST_SENT` | `OllamaClient` | GUI | `{ model, prompt_length }` |
| `LLM_RESPONSE_RECEIVED` | `OllamaClient` | `BrainController`, GUI | `{ OllamaResponse }` |
| `COMMAND_PLAN_READY` | `BrainController` | `CommandEngine`, GUI | `{ CommandPlan }` |
| `SAFETY_CONFIRMATION_REQ` | `SafetyGate` | TTSEngine, STTEngine, GUI | `{ prompt_text, command }` |
| `SAFETY_CONFIRMED` | `SafetyGate` | `CommandEngine`, GUI | `{ command, timestamp }` |
| `SAFETY_DENIED` | `SafetyGate` | TTSEngine, GUI | `{ command, reason }` |
| `EXECUTION_STARTED` | `CommandEngine` | GUI | `{ executor, action }` |
| `EXECUTION_COMPLETE` | `CommandEngine` | `BrainController`, `MemoryMgr`, GUI | `{ ExecutionResult }` |
| `TTS_SPEAK_REQUEST` | `BrainController` | `TTSEngine` | `{ text, priority }` |
| `TTS_SPEAKING_STARTED` | `TTSEngine` | GUI | `{ text_preview }` |
| `TTS_SPEAKING_FINISHED` | `TTSEngine` | `WakeWordListener` (re-arm), GUI | `{ duration_ms }` |
| `MODE_CHANGED` | `ModeMonitor` | `TTSEngine`, `BrainController`, GUI | `{ mode: ONLINE | OFFLINE }` |
| `SYSTEM_ERROR` | Any module | GUI, Logger | `{ error, module, severity }` |

---

### 6.3 Pipeline State Machine

The pipeline has exactly **6 mutually exclusive states**. Only one state is active at any time:

| State | Active Module | Entry Trigger | Exit Trigger |
| --- | --- | --- | --- |
| `IDLE` | `WakeWordListener` | System start / `TTS_SPEAKING_FINISHED` | `WAKE_WORD_DETECTED` |
| `LISTENING` | `STTEngine` | `WAKE_WORD_DETECTED` | `RECORDING_STOPPED` (silence timeout) |
| `CLASSIFYING` | `IntentRouter` | `TRANSCRIPTION_COMPLETE` | `INTENT_CLASSIFIED` |
| `THINKING` | `BrainController` | `INTENT_CLASSIFIED` | `COMMAND_PLAN_READY` |
| `EXECUTING` | `CommandEngine` | `COMMAND_PLAN_READY` | `EXECUTION_COMPLETE` |
| `SPEAKING` | `TTSEngine` | `TTS_SPEAK_REQUEST` | `TTS_SPEAKING_FINISHED` |

> **Note:** `SAFETY_CONFIRMATION` interrupts `EXECUTING` and creates a sub-loop: `EXECUTING → AWAITING_CONFIRM → (EXECUTING or IDLE)`. This is the only valid state re-entry path.
> 

---

### 6.4 Threading Model

| Component | Thread Type | Rationale |
| --- | --- | --- |
| `WakeWordListener` | Daemon thread | Must run continuously — killed when main exits |
| `ModeMonitor` | Daemon thread | Background polling — killed when main exits |
| `STT` (Whisper) | Worker thread (blocking) | Whisper inference blocks — must not freeze event loop |
| `OllamaClient` | Worker thread (blocking) | LLM inference blocks — can take 5–30 seconds on CPU |
| Executor actions | Worker thread (blocking) | Git/Docker calls can block for several seconds |
| `TTSEngine` | Worker thread + queue | Audio output must be non-blocking to caller |
| PyQt6 GUI | **Main thread** | Qt requires main thread — all other work goes to workers |
| `EventBus.emit()` | Caller's thread | Handlers execute synchronously in emitter's thread |

---

### 6.5 Configuration Contract (`config.yaml`)

Every configurable value in AURA must have an entry in `config.yaml`. Hard-coded values in source files are a **violation** of this contract.

```yaml
# config.yaml — complete required schema

aura:
  version: "1.0"
  language: "en"

models:
  fast:       "llama3.2:3b-q4_0"
  general:    "mistral:7b-instruct-q4_0"
  reasoning:  "llama3:8b-q4_0"
  code:       "deepseek-coder:7b-q4_0"
  vision:     "llava:7b"
  embeddings: "nomic-embed-text"

routing:
  complexity_threshold:      50
  rag_confidence_threshold:  0.50   # nomic-embed-text; 0.72 filtered all real matches (see VERIFICATION_LOG.md)
  rag_rank_margin:           0.03   # exclude rank-2+ chunks far below top-1 similarity
  realtime_warning:          true
  intent_timeout_seconds:    10
  intent_max_retries:        3

stt:
  model:           "base"       # tiny | base | small | medium | large
  silence_timeout: 2.0          # seconds of silence to end recording
  max_recording:   30           # maximum recording length in seconds

safety:
  confirmation_timeout: 8       # seconds to wait for voice confirmation
  audit_log: "logs/safety_audit.log"

memory:
  persist_path: ".aura/memory"
  max_results:  3
  purge_days:   90

ollama:
  base_url: "http://localhost:11434"
  timeout:  60
  retries:  3
```

---

## Appendix — Phase 2 Pre-Flight Checklist

Before writing a single line of Phase 2 code, confirm all of the following:

| # | Check | Status |
| --- | --- | --- |
| 01 | Python 3.11 venv created and active | ☐ TODO |
| 02 | `config.yaml` exists with all required keys from Section 6.5 | ☐ TODO |
| 03 | Ollama installed and running (`ollama serve`) | ☐ TODO |
| 04 | All 6 models pulled (mistral, deepseek-coder, llama3.2, llama3, llava, nomic) | ☐ TODO |
| 05 | Whisper model pre-downloaded (not fetched at first run) | ☐ TODO |
| 06 | `EventBus` class implemented with subscribe/emit/unsubscribe | ☐ TODO |
| 07 | Full folder skeleton created with `__init__.py` in every package | ☐ TODO |
| 08 | `config.py` loads and validates `config.yaml` — exits clearly on errors | ☐ TODO |
| 09 | `OllamaClient` returns `OllamaResponse` and raises `OllamaUnavailableError` | ☐ TODO |
| 10 | `IntentRouter` returns `IntentObject` with fallback to `UNKNOWN` | ☐ TODO |
| 11 | `SafetyGate` implemented before any executor code is written | ☐ TODO |
| 12 | `safety_audit.log` path configured and writable | ☐ TODO |

---

> ⚡ *AURA Engineering Spec v1.0 — Built offline. Powered locally. Yours completely.*
