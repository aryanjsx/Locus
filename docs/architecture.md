# AURA Architecture

AURA's architecture is designed so that the command execution layer remains untouched as new input channels (voice), reasoning engines (LLM), and output channels (TTS, GUI) are added.

**Phase status** (see [`../ROADMAP.md`](../ROADMAP.md)):

- **Phase 0** — Project Core (INFRA): **COMPLETED**
- **Phase 1** — Foundation (System Plugin): **COMPLETED**
- **Phase 2** — Voice + Intelligence Router: **IN PROGRESS**

The **Phase-0 execution backbone** (secure dispatch, policy, argv-based subprocess, path safety) and the **Phase-1 security pipeline** (non-bypassable `CommandRegistry`, sandboxed worker, audit chain, rate limiter, safety gate, plugin manifest binding) support the CLI and every future channel. The intent layer, command registry, policy gate, and LLM backend abstraction are already in place so the in-progress Whisper listener and Ollama model slot into the pipeline without touching the execution core.

---

## Data Flow

### Phase 0 + Phase 1 — CLI (COMPLETED)

```
User input (stdin)  OR  one-shot: python aura.py "<command>"
       │
       ▼
┌──────────────────┐
│    aura.py       │  Interactive: InputSource (stdin) / OutputSink (stdout)
│    (CLI REPL)    │  One-shot: argv joined → dispatch() → print result
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│  dispatcher.py   │  parse_intent() converts text → Intent
│                  │  execute_intent() routes Intent → handler
└──────┬───────────┘
       │  ┌──────────────┐
       │  │  policy.py   │  Validates intent before execution
       │  └──────────────┘
       │
       ├──► file_manager       (create / delete / rename / move / search)
       ├──► process_manager    (run command / list / kill / CPU / RAM via psutil)
       ├──► npm_executor       (npm install / npm run — argv only, shutil.which)
       ├──► system_check       (check system health)
       ├──► project_scaffolder (create project)
       ├──► log_reader         (show logs)
              └──► show_help          (built-in Phase 0 help text)
              │
              │  File and npm handlers resolve paths through path_utils
              │  before touching the filesystem or project cwd
              │
              ▼
┌──────────────────┐
│   path_utils.py  │  ~ expansion, smart keywords, validate_not_protected
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│   logger.py      │  Writes structured entry to logs/aura.log
└──────┬───────────┘
       │
       ▼
   Console output
   (result string via OutputSink or print in one-shot mode)
```

**Natural phrases:** The dispatcher recognises short monitor phrases (e.g. `cpu`, `ram`, `processes`, `show processes`) and maps them to registry actions such as `get_cpu_usage`, `get_ram_usage`, and `list_processes`, implemented in `process_manager.py`.

### Phase 2 — Voice + LLM (IN PROGRESS)

```
Microphone input
       │
       ▼
┌──────────────────┐
│  Whisper STT     │  Transcribes audio → text
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│  LLMBrain        │  Sends text to Ollama → structured Intent
│  + OllamaBackend │  Uses command registry for prompt context
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│ execute_intent() │  Same pipeline as CLI — no changes needed
│  + policy.py     │
└──────┬───────────┘
       │
       ▼
   CommandResult
       │
       ├──► Console output
       └──► Piper TTS (speak result aloud)
```

---

## Module Responsibilities

### core/intent.py

The `Intent` dataclass represents a parsed user intention — the bridge between raw text (or LLM output) and the execution layer. It carries an `action` string (e.g. `"file.create"`), a dict of `args` that map to handler parameter names, the `raw_text` that produced it, the `source` channel (`"cli"`, `"llm"`, `"voice"`), and a `confidence` score. The Intent is frozen (immutable after creation) and serves as the single contract between input parsing and command execution.

### core/policy.py

The `CommandPolicy` class is the centralized safety gate. Every intent passes through `validate_intent()` before a handler is invoked — both in the CLI dispatcher and (via the in-progress Phase 2 LLM pipeline) in the LLM pipeline.

For **`process.shell`** (the `run command` path), validation uses a **hybrid model**:

- **Denylist:** exact destructive strings and dangerous substrings are rejected.
- **Allowlist:** the first argv token must normalise to a name in `ALLOWED_COMMANDS` (e.g. `git`, `python`, `npm`).

Commands are split with `split_command_string()` (no shell); execution uses argv lists with `shell=False` in `process_manager.safe_run_command`. Shell-related file-path safety for destructive file ops is handled separately by `path_utils.validate_not_protected()`. **`npm.install` / `npm.run`** skip shell-string checks in policy because npm is invoked only via fixed argv in `npm_executor` after path validation.

`process_manager.run_shell_command()` calls policy again before subprocess execution as defense in depth.

### core/context.py

The `AppContext` dataclass bundles cross-cutting concerns — config, policy, and session state — into a single injectable object. Phase 2 (in progress) adds the LLM backend and conversation history here so that components receive one object instead of importing scattered globals.

### core/backends/

The LLM backend abstraction layer. `base.py` defines the `LLMBackend` ABC with `complete()` and `is_available()` methods. `ollama_backend.py` provides a stub that returns canned responses so the pipeline can be tested end-to-end before a real Ollama server is connected. `factory.py` reads config and returns the appropriate backend instance.

### core/config_loader.py

Loads settings from `config.yaml` (user-local, gitignored) with fallback to `config.example.yaml` (tracked template). Supports dot-notation access (`get("logging.level")`), deep-merges user YAML into built-in defaults, applies **environment overrides**, and caches the result.

Optional environment variables (applied after YAML merge):

| Variable | Effect |
|----------|--------|
| `AURA_LOG_PATH` | Overrides `logging.file` |
| `AURA_SHELL_TIMEOUT` | Overrides `shell.timeout` (integer seconds) |
| `AURA_PROTECTED_PATHS` | Comma-separated list replacing `paths.protected` |

Every other module reads settings through this loader. The loader logs warnings with `logging.getLogger("aura.config")` (no `print` for parse failures).

### core/io.py

Defines `InputSource` and `OutputSink` abstract base classes so that the main loop can read commands and emit results through any channel. Phase 1 (completed) provides `StdinInput` and `StdoutOutput`. Phase 2 (in progress) adds a Whisper-based input source and a Piper TTS output sink — the dispatcher and handlers require zero changes.

### core/result.py

The `CommandResult` dataclass is the uniform return type for every handler. It carries `success` (bool), `message` (human-readable text), `data` (optional structured payload for programmatic consumers), and `command_type` (dot-namespaced label like `"file.create"`).

### dispatcher.py

The command router. `parse_intent()` converts raw text into a structured `Intent` using keyword matching (files, processes, npm, system health, monitor phrases, `help` / `--help`, etc.). `execute_intent()` looks up the handler in `COMMAND_REGISTRY`, validates via the policy gate, and calls the handler with `**intent.args`. The `dispatch(command)` entry point calls `parse_intent()` then `execute_intent()`. `get_available_commands()` returns metadata for every registered command, enabling LLM prompt generation and dynamic help text.

### path_utils.py

The centralized path resolution layer. Every module that touches the filesystem funnels user-supplied path strings through `resolve_path()`, which strips whitespace, expands smart-location keywords (`desktop`, `downloads`, `documents`), expands `~` via `pathlib.Path.expanduser()`, and resolves to an absolute path. **`validate_not_protected()`** reads protected roots from **`get_config("paths.protected")`** on each call so `AURA_PROTECTED_PATHS` and config reloads stay effective. It blocks destructive operations on system-critical directories (e.g. `C:\Windows`, `/usr`) and filesystem roots.

### file_manager.py

Provides five atomic file operations — create, delete, rename, move, and glob-search. Each function accepts a raw path string, calls `resolve_path()` to obtain an absolute `pathlib.Path`, performs the operation, and returns a `CommandResult`. Delete operations additionally check `validate_not_protected()` before unlinking. Move operations auto-detect whether the destination is a directory and place the file inside it.

### process_manager.py

Wraps **`subprocess.run()`** with **`shell=False`** and **`psutil`** for process inspection and termination.

- **`safe_run_command(cmd: list[str], ...)`** — the only subprocess path for external commands; always passes an argv list, never a shell string.
- **`run_shell_command(command: str)`** — splits the user string with **`split_command_string()`** (from `core.policy`), validates with **`CommandPolicy`** (denylist + allowlist), then invokes **`safe_run_command`**.
- **`get_cpu_usage` / `get_ram_usage` / `list_running_processes`** — use **psutil** only (no subprocess for those metrics).

On **all platforms**, including Windows, execution is **argv-based** — there is **no** `shell=True` on user-controlled command execution.

### npm_executor.py

Runs **`npm install`** and **`npm run <script>`** inside a validated project directory. Resolves the npm executable with **`shutil.which("npm") or shutil.which("npm.cmd")`** (Windows batch shim). Builds argv **`[npm_exec, "install"]`** or **`[npm_exec, "run", script]`** and delegates to **`safe_run_command`** — never `shell=True`.

### system_check.py

Probes the local environment for commonly required developer tools (Python, Git, Node, Docker) by running their `--version` probes via **`safe_run_command`** with split argv. Returns a structured report. The tool list is loaded from config.

### logger.py

Configures a stdlib `logging.Logger` with two handlers: a `RotatingFileHandler` that writes every entry to `logs/aura.log` with automatic rotation, and a `StreamHandler` for the console (WARNING and above). `get_logger()` is idempotent — calling it multiple times with the same name returns the same configured instance. Modules typically use names like `aura.dispatcher`, `aura.process_manager`.

### core/llm_brain.py

The `LLMBrain` class accepts natural-language text and returns a structured `Intent`. It wraps an `LLMBackend` and — as part of the in-progress Phase 2 work — builds a prompt from the command registry, sends it to the model, and parses the structured response. Currently a stub that returns a low-confidence passthrough intent when the backend is unavailable.

---

## Design Decisions

### Why pathlib over os.path

`pathlib.Path` provides an object-oriented API that makes path manipulation readable and chainable (`path.parent.mkdir(parents=True)`), handles platform differences internally, and integrates with `expanduser()`, `resolve()`, and `rglob()` without importing separate functions.

### Why centralized path_utils instead of per-module resolution

A single `resolve_path()` entry point guarantees that every module — current and future — handles `~`, `desktop/`, and protected-path validation identically. Adding a new smart keyword or blocked directory requires changing exactly one file (plus config / env for protected roots).

### Why stdlib logging over third-party

The stdlib `logging` module supports file handlers, formatters, log levels, and handler composition out of the box. Introducing a third-party logger would add a dependency with no material benefit at this stage. If Phase 5's memory layer requires structured JSON logs, the handler can be swapped without changing any call site.

### Why Intent + Registry instead of direct dispatch

The original dispatcher matched keywords and called handlers directly in a long if-chain. The intent-based architecture separates parsing from execution: `parse_intent()` produces a data object, and `execute_intent()` consumes it. This means an LLM can produce the same `Intent` structure that the text parser produces, and both flow through the same execution path — policy check, registry lookup, handler call. The command registry also enables `get_available_commands()`, which the in-progress Phase 2 prompt builder uses to tell the LLM what AURA can do.

### Why argv-only subprocess

Passing argument lists with `shell=False` eliminates shell metacharacter injection (e.g. `&`, `|`) on Windows and Unix. Policy further restricts which executables may be started via the generic `run command` path.

---

## Phase 2 — What Remains (IN PROGRESS)

The following architecture landed in Phase 0 + Phase 1 and is ready for Phase 2 to build on:

- **Intent system** — `Intent` dataclass, `parse_intent()`, `execute_intent()`
- **Command registry** — non-bypassable `CommandRegistry` with `get_available_commands()`
- **Policy gate** — `CommandPolicy.validate_intent()` blocks dangerous operations
- **LLM backend abstraction** — `LLMBackend` ABC with `OllamaBackend` stub
- **LLM brain** — `LLMBrain.process()` stub ready for real inference
- **I/O abstraction** — `InputSource` / `OutputSink` contracts

What Phase 2 still needs to build:

- **Whisper STT** — a new `InputSource` implementation that listens to the microphone and yields transcribed text
- **Ollama integration** — replace the `OllamaBackend` stub with real HTTP calls to a local Ollama server
- **Prompt engineering** — build the system prompt from `get_available_commands()` and parse structured LLM output into `Intent` objects
- **Piper TTS** — a new `OutputSink` implementation that speaks `CommandResult.message` aloud
- **Async main loop** — the current blocking `while True` loop needs an async variant for concurrent voice listening and command processing
