# Locus — Scope (MVP)

> Status: **Draft v0.2** · Owner: Aryan · Last updated: 2026-09-30
> Companion: [02-command-catalog.md](02-command-catalog.md)

## 1. Vision

Locus is a **private, offline-first desktop assistant** that anyone can install on their PC. You type or speak a command, and Locus *does* it (checks system status, opens apps, manages files) or answers the question. Nothing leaves your machine unless you switch an online feature on.

**Name:** Locus is the single name for the app, the assistant and the repo. It replaces v1's AURA/Kommy split. Future wake word: "Hey Locus".

## 2. Target users

| | |
|---|---|
| **Final audience** | Anyone who wants a virtual assistant on their own device, including non-technical users (via a `.exe` installer) |
| **MVP audience** | Technical early adopters who can install with `uv`. The `.exe` comes after the MVP is stable. |
| **Language** | English only (v1) |

## 3. Platform and hardware

| | Minimum | Recommended |
|---|---|---|
| OS | Windows 10/11 x64 | Windows 11 x64 |
| RAM | **8 GB** | **16 GB** |
| GPU | None required | None required |
| CPU | 4 cores with AVX2 (≈2018+) | 6–8 cores (≈2020+) |
| Free disk | ~5 GB | ~10 GB |
| Locus RAM budget | **≤ 3 GB** | **≤ 5 GB** |

Linux and macOS are **future** targets. Anything OS-specific (volume, app launching, power, recycle bin) sits behind an interface so it can be swapped per OS later.

## 4. MVP scope

About 20 user-facing actions in three groups, plus 3 meta actions. The full list and test phrases are in the [command catalog](02-command-catalog.md).

| Group | Actions |
|---|---|
| **System info** | CPU, RAM, disk, battery, top processes, date/time |
| **Apps, files & power** | open/close app, open folder, create file/folder, rename, move, delete (to recycle bin), search files, lock, shutdown, restart |
| **Q&A** | General questions and short code answers from the local LLM |
| *Meta* | help ("what can you do"), cancel ("stop"), unsupported ("I can't do that yet" / "that needs the internet") |

### Interaction
- **Text first.** Voice is just another input: speech-to-text, then the *same* pipeline as text.
- **Push-to-talk** hotkey for voice. The wake word comes in a later milestone.
- **One-shot:** each command stands alone, with no conversation memory.
- **One action per command.** "Open Chrome and close Notepad" is refused politely in the MVP.

## 5. Non-goals (MVP)

Not building yet, and no stub folders for them:

- Wake word, conversation memory, RAG / document indexing
- Git, Docker, email, calendar, music, web automation, vision
- Multi-step plans, arbitrary shell commands
- Languages other than English, Linux/macOS
- GUI beyond a minimal window or console, plugin marketplace

## 6. Safety policy

### Risk tiers

| Tier | Rule | Examples |
|---|---|---|
| **Read-only** | Just do it | CPU, RAM, search files, Q&A |
| **Reversible** | Do it, then say what was done | Open app, create/rename/move file, lock PC |
| **Check first** | Ask a question; the user answers by voice, text or click within **15 s**; no answer cancels | Close app ("Have you saved your work in Notepad?") |
| **Destructive** | **Requires manual confirmation** (below) | Delete file, shutdown, restart |

### Closing apps
- Locus asks whether the work is saved. "Yes", typed or spoken, or a click closes the app. "No" or silence for 15 s cancels.
- Apps are always closed **gracefully** (a normal close request), never force-killed. If there is still unsaved work, the app's own "Save changes?" dialog appears as a second safety net.

### Destructive confirmation flow
1. The user gives the command (typed or spoken). This is the first confirmation.
2. Locus **shows and speaks** exactly what it will do: "Delete `notes.txt` from Documents (goes to Recycle Bin)."
3. The user must **press the confirm key or click Confirm** in the Locus window. This is the second, manual confirmation.
   - **A spoken "yes" is not accepted.**
   - Esc, Cancel or a **15-second timeout** cancels.
4. The outcome is written to the audit log.

### Hard rules
- **Deletes always go to the Recycle Bin.** There is no permanent delete in the MVP. If a location has no Recycle Bin (some USB drives, network shares), Locus **refuses** rather than deleting permanently.
- **One safety layer for every input channel.** Text and voice use the identical path, and nothing can reach an action without going through it.
- **No folder restrictions.** Locus can act on any file or folder the user names, with the normal Windows permissions of the logged-in user (it never asks for admin rights).
- **Extra warning for system locations.** Destructive actions on Windows, Program Files, drive roots or whole user folders still work, but the confirmation screen shows a clear red warning, e.g. "This is a Windows system folder. Deleting it may stop your PC from working."
- **Only the user's own words can trigger actions.** Text Locus reads (LLM output, files, web content later) can never cause an action.
- **No arbitrary shell commands.**

## 7. Privacy and online features

- **Offline by default.** Every MVP feature works with no network connection.
- **All online features are OFF by default.** The user switches each one on individually, and Locus says when it went online.
- **The MVP has no online features.** Internet-only requests get "I can't look that up yet."
- Post-MVP online features: **web search first**, then an online neural voice (Edge TTS).

### When a request needs the internet
| Situation | Locus's reply |
|---|---|
| The feature exists but is **off** | Explains it needs the internet and **asks to turn it on**: "Checking the weather needs web search, which is off. Turn it on?" Turning it on uses the manual Confirm click, since it changes a privacy setting. |
| The feature is **on**, but the PC is offline | "You're offline right now, so I can't check that." |
| The feature doesn't exist yet | "I can't look that up yet. It needs the internet, and online features aren't available in this version." |

The LLM **never** makes up live information (weather, prices, latest versions) from its training data.
- **The only other network use** is downloading models, which the user starts explicitly in the setup screen.
- No telemetry.

## 8. Quality targets

| Metric | Target |
|---|---|
| Router accuracy on the command catalog | **≥ 95%** (16 GB tier), **≥ 90%** (8 GB tier) |
| Destructive actions executed without manual confirm | **0** (hard requirement) |
| Typed system command → result | < 1 s (fast path) / < 2 s (via LLM) |
| Push-to-talk release → first spoken word (Q&A) | < 2.5 s (16 GB tier) / < 4 s (8 GB tier) |
| Peak RAM | Within the budget in §3 |

Everything is measured on the **weakest machine available**, not only the developer's.

## 9. Distribution (post-MVP)

- A **Windows `.exe` installer** once the MVP is stable.
- **First-run setup:** detect RAM, CPU (AVX2) and free disk, **recommend models for that tier**, and download them only after the user agrees.
- **The installer bundles the LLM engine (`llama.cpp`).** Users don't install anything else.
- Implications to decide early (see open decisions):
  - How to embed `llama.cpp`: the prebuilt `llama-server` binary run as a child process, or the `llama-cpp-python` bindings.
  - Packaging tool (PyInstaller / Nuitka / Briefcase) and whether native libraries (ctranslate2, onnxruntime) package cleanly.
  - Code signing, so Windows SmartScreen doesn't block the installer.
  - License check for every bundled component.

## 10. Decisions log

| # | Decision | Date |
|---|---|---|
| D1 | Windows first; Linux/macOS later | 2026-09-30 |
| D2 | Min 8 GB RAM / recommended 16 GB, no GPU | 2026-09-30 |
| D3 | Offline-first; all online features off by default | 2026-09-30 |
| D4 | Text first; voice = STT then the same pipeline | 2026-09-30 |
| D5 | Push-to-talk for MVP; wake word later | 2026-09-30 |
| D6 | One-shot, one action per command | 2026-09-30 |
| D7 | Destructive = command + manual key/click confirm; spoken "yes" not accepted | 2026-09-30 |
| D8 | Deletes go to the Recycle Bin | 2026-09-30 |
| D9 | Single pipeline: input adapters → router → safety gate → action registry → output adapters | 2026-09-30 |
| D10 | LLM routing via structured output (JSON schema, action-name enum) plus exact-match fast path | 2026-09-30 |
| D11 | Threads + queue (no asyncio) | 2026-09-30 |
| D12 | Plugins = Python modules registering actions (name, schema, risk tier) | 2026-09-30 |
| D13 | English only for v1 | 2026-09-30 |
| D14 | Tooling: uv, Ruff, Pyright, pytest, pre-commit, GitHub Actions, Conventional Commits | 2026-09-30 |
| D15 | Post-MVP `.exe` with hardware detection and model recommendation | 2026-09-30 |
| D16 | Destructive confirm must be a manual key/click within 15 s; timeout cancels | 2026-09-30 |
| D17 | No folder restrictions; red warning for system locations; refuse if no Recycle Bin | 2026-09-30 |
| D18 | LLM engine = `llama.cpp`, bundled in the installer (replaces Ollama) | 2026-09-30 |
| D19 | Requests needing the internet: explain, and offer to turn the feature on if it exists | 2026-09-30 |
| D20 | Closing an app is "check first", not destructive: ask about unsaved work, 15 s, graceful close only | 2026-09-30 |
| D21 | No online features in the MVP: internet-only requests get "I can't look that up yet"; web search is the first post-MVP feature | 2026-09-30 |
| D22 | Name: **Locus** (app, assistant and repo); replaces AURA/Kommy | 2026-09-30 |

## 11. Open decisions (settled by experiments)

| # | Question | How we decide |
|---|---|---|
| O1 | LLM model per tier (Qwen3.5 2B / Gemma 4 E2B / Llama 3.2 3B; Qwen3.5 4B / Gemma 4 E4B / Phi-4-mini) | Experiment 1: accuracy + latency on the catalog |
| O2 | STT: faster-whisper `base.en` / `small.en` vs Moonshine | Experiment 2 |
| O3 | TTS: Piper vs Kokoro (16 GB tier) | Experiment 3 |
| O4 | Embed `llama.cpp` as `llama-server` child process (recommended) or `llama-cpp-python` bindings | ADR during experiment 1 |
| O5 | Confirm key: Enter vs a dedicated combo (e.g. Ctrl+Enter) | Usability test |
