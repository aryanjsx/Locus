<div align="center">

# Locus

### A private, offline-first assistant for your PC

**Type or speak a command. Locus does it, on your machine, without sending anything to the cloud.**

![Status](https://img.shields.io/badge/status-planning-blue)
![Platform](https://img.shields.io/badge/platform-Windows-0078D6)
![License](https://img.shields.io/github/license/aryanjsx/AURA)

[Why Locus](#why-locus) · [MVP scope](#mvp-scope) · [Safety](#safety) · [Requirements](#system-requirements) · [Tech stack](#tech-stack) · [Roadmap](#roadmap) · [Docs](#documentation)

</div>

---

> **Project status: planning.** Locus is a fresh restart of this project, which was previously called AURA / Kommy. The requirements and scope are done; **there is no working Locus code yet.** The next step is a set of experiments to choose the models, then the first code milestone.
>
> The v1 (AURA / Kommy) code is still in this repository for reference and will be replaced. See [About v1](#about-v1).

---

## Why Locus

Most AI assistants are a chat window connected to someone else's server. Locus is different:

- **Private by design:** everything runs locally. No accounts, no API keys, no telemetry.
- **Offline first:** every core feature works with no internet connection. Online features are **off by default**, and you switch each one on yourself.
- **Does things, not just talks:** checks your system, opens and closes apps, and manages files, as well as answering questions.
- **Safe with your data:** anything destructive needs your manual confirmation, and deleted files always go to the Recycle Bin.
- **Runs on ordinary laptops:** 8 GB of RAM is enough, no GPU needed.

## MVP scope

About 20 actions in three groups. Each command does one thing. The full list, with test phrases, is in the [command catalog](docs/locus/02-command-catalog.md).

| Group | What you can ask |
|---|---|
| **System info** | CPU, RAM, disk space, battery, top processes, date and time |
| **Apps, files & power** | Open/close apps, open folders, create/rename/move/delete files and folders, search files, lock, shut down, restart |
| **Q&A** | General questions and short code answers from a local language model |

**How you talk to it:**
- **Text first.** Voice uses exactly the same pipeline: speech is transcribed to text, then handled like typed text.
- **Push-to-talk** hotkey for voice. A wake word ("Hey Locus") comes later.
- **English only** in v1.

**Not in the MVP:** wake word, conversation memory, web search, Git/Docker/email/music integrations, multi-step commands, Linux/macOS. If you ask for something that needs the internet, Locus says *"I can't look that up yet"*; it never makes up live information.

## Safety

| Risk level | What Locus does | Examples |
|---|---|---|
| **Read-only** | Does it straight away | "What's my CPU?", "Find my resume" |
| **Reversible** | Does it, then tells you what it did | "Open Chrome", "Create a folder called projects" |
| **Check first** | Asks a question; answer within 15 s or it cancels | "Close Notepad" → *"Have you saved your work?"* |
| **Destructive** | Shows exactly what will happen; **you must press the confirm key or click Confirm** within 15 s | Delete a file, shut down, restart |

- **A spoken "yes" is never enough for destructive actions.** Confirmation is always a key press or click.
- **Deletes go to the Recycle Bin.** If a drive has no Recycle Bin, Locus refuses rather than deleting permanently.
- **System locations** (Windows, Program Files, whole user folders) get a clear red warning before you confirm.
- Apps are always closed normally, never force-killed, so their own "Save changes?" dialog still appears.
- Only **your own words** can trigger actions. Text that Locus reads can't.
- Every destructive action is written to an audit log.

## System requirements

| | Minimum | Recommended |
|---|---|---|
| OS | Windows 10/11 (64-bit) | Windows 11 (64-bit) |
| RAM | 8 GB | 16 GB |
| GPU | Not required | Not required |
| CPU | 4 cores with AVX2 (≈2018+) | 6–8 cores (≈2020+) |
| Free disk | ~5 GB | ~10 GB |

The planned Windows installer (`.exe`) will check your hardware on first run and **recommend the right models for your machine**. It downloads them only after you agree.

## Tech stack

Planned. The model and engine choices are confirmed by benchmarks during the experiment phase.

| Layer | Choice |
|---|---|
| Language | Python 3.12 |
| LLM engine | [`llama.cpp`](https://github.com/ggml-org/llama.cpp), bundled in the installer |
| LLM models (candidates) | 8 GB: Qwen3.5 2B / Gemma 4 E2B / Llama 3.2 3B · 16 GB: Qwen3.5 4B / Gemma 4 E4B / Phi-4-mini |
| Command routing | Structured JSON output (schema-constrained) + exact-match fast path for common commands |
| Speech-to-text | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (`base.en` / `small.en`); Moonshine as an alternative |
| Text-to-speech | Piper (default), Kokoro (optional on 16 GB), Windows voices as fallback |
| System access | `psutil`, `send2trash`, `pycaw`, `subprocess` (argument lists only, no shell) |
| Schemas and config | Pydantic v2, TOML |
| Audio / hotkey | `sounddevice`, `pynput` |
| Tooling | uv, Ruff, Pyright, pytest, pre-commit, GitHub Actions, Conventional Commits |

**Architecture:** one pipeline for every input:

```
Input (text / voice) → Router → Safety gate → Action registry → Output (screen / speech)
```

Features are plugins: Python modules that register actions, each with a name, a parameter schema and a risk level.

## Roadmap

| Stage | Deliverable | Status |
|---|---|---|
| **Planning** | Scope, safety policy, stack, command catalog | ✅ Done |
| **Experiments** | Benchmark LLM routing accuracy, speech-to-text and TTS speed, and peak RAM on real hardware ([harness](experiments/README.md)) | 🔬 In progress |
| **M0: skeleton** | Typed "what's my CPU" → spoken answer, through the full pipeline | Planned |
| **M1: voice** | Push-to-talk voice input | Planned |
| **M2: Q&A** | Local LLM answers streamed to speech | Planned |
| **M3: safety** | Confirmation flow and audit log | Planned |
| **M4: MVP** | Every command in the catalog working end to end | Planned |
| **M5: installer** | Windows `.exe` with hardware check and model recommendation | Planned |
| **Later** | Web search (opt-in), wake word, Linux/macOS, more integrations | Future |

A feature counts as done only when its catalog tests pass end to end through the real app. This README lists only features that meet that bar.

## Documentation

- [Scope (MVP)](docs/locus/01-scope.md): vision, hardware tiers, safety and privacy policy, quality targets, decisions log
- [Command catalog](docs/locus/02-command-catalog.md): every MVP action and its test phrases, including tricky near-misses
- [Experiments](experiments/README.md): the stage 2 benchmarks (router accuracy, speech-to-text, TTS, peak RAM) and how to run them

## About v1

This repo originally contained **AURA / Kommy**, a first attempt at the same idea. It's being restarted as Locus because:

- the voice pipeline and the text CLI were two separate systems, and the voice path skipped the security layer;
- several advertised voice commands didn't actually work end to end;
- the regex-based command routing misread everyday phrases.

The v1 code is still here for reference and will be removed as Locus replaces it.

## Contributing

Locus is at the planning stage. Feedback on the [scope](docs/locus/01-scope.md) and [command catalog](docs/locus/02-command-catalog.md) is very welcome, especially new test phrases and tricky edge cases. Please open an [issue](https://github.com/aryanjsx/AURA/issues).

## License

MIT, see [LICENSE](LICENSE).

<div align="center">

**Locus**: your assistant, on your machine.

Built by [@aryanjsx](https://github.com/aryanjsx)

</div>
