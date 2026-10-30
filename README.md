
<div align="center">

# Locus

### A private, offline-first assistant for your PC

**Type or speak a command. Locus does it, on your machine, without sending anything to the cloud.**

![Status](https://img.shields.io/badge/status-planning-blue)
![Platform](https://img.shields.io/badge/platform-Windows-0078D6)
![License](https://img.shields.io/github/license/aryanjsx/AURA)
[![XERJ Corpus Hub contributor](https://hub.xerj.org/contributors/aryanjsx/badge.svg)](https://hub.xerj.org/contributors/aryanjsx)


[Website](https://aryanjsx.github.io/Locus/) · [Why Locus](#why-locus) · [MVP scope](#mvp-scope) · [Safety](#safety) · [Requirements](#system-requirements) · [Tech stack](#tech-stack) · [Roadmap](#roadmap) · [Docs](#documentation)

</div>

## Why Locus?

Personal AI agents are exploding in popularity, but nearly all of them still route intelligence through cloud APIs. Your "personal" AI continues to depend on someone else's server. At the same time, our [Intelligence Per Watt](https://www.intelligence-per-watt.ai/) research showed that local language models already handle 88.7% of single-turn chat and reasoning queries, with intelligence efficiency improving 5.3× from 2023 to 2025. The models and hardware are increasingly ready. What has been missing is the software stack to make local-first personal AI practical.

Locus is that stack. It is a framework for local-first personal AI, built around three core ideas: shared primitives for building on-device agents; evaluations that treat energy, FLOPs, latency, and dollar cost as first-class constraints alongside accuracy; and a learning loop that improves models using local trace data. The goal is simple: make it possible to build personal AI agents that run locally by default, calling the cloud only when truly necessary. Locus aims to be both a research platform and a production foundation for local AI, in the spirit of PyTorch.

## Installation

Pick your platform and run one command. Each installer handles [uv](https://docs.astral.sh/uv/), the Python venv, Ollama, and a starter model — about 3 minutes on broadband.

| Platform | One-liner |
|---|---|
| **macOS · Linux · WSL2** | `curl -fsSL https://locus.github.io/Locus/install.sh \| bash` |
| **Native Windows** | `irm https://locus.github.io/Locus/install.ps1 \| iex` |
| **Desktop GUI** | Download `.exe` / `.dmg` / `.deb` / `.rpm` / `.AppImage` from the [latest release](https://github.com/locus/Locus/releases) |

Then `jarvis` to start. The Rust extension and larger models continue downloading in the background; `jarvis doctor` shows status.

Platform-specific notes (WSL2 setup, native-Windows scheduled-task service, desktop prerequisites, manual / contributor install): see the [installation docs](https://locus.github.io/Locus/getting-started/install/).

## Quick Start

```bash
jarvis                          # start chatting (default: chat-simple)
jarvis gui                      # start the graphical browser interface
jarvis init --preset <name> --force  # replace config with a starter preset
```

`jarvis gui` starts the local API server and frontend, then opens the graphical
chat interface in your default browser. It requires Node.js 22+ and is
available from a source checkout; packaged desktop installers are available
from the [latest release](https://github.com/locus/Locus/releases).

> Prefix `jarvis ...` with `uv run`, or `source .venv/bin/activate` first.

#### Run from a Windows source checkout

Install [uv](https://docs.astral.sh/uv/), [Node.js 22+](https://nodejs.org/),
and [Ollama](https://ollama.com/), then run these commands from the repository
root in PowerShell:

```powershell
uv sync --extra desktop
Push-Location frontend
npm install
Pop-Location
ollama pull qwen3:0.6b
uv run jarvis gui
```

If Ollama is not already running, start it in another terminal with
`ollama serve`. The GUI should open in your browser.

To run the test suite from the repository root:

```powershell
uv sync --extra dev
uv run pytest tests/ -v
```

| Preset | What it does |
|---|---|
| `morning-digest-mac` / `morning-digest-linux` / `morning-digest-minimal` | Spoken daily briefing from email, calendar, health, news |
| `deep-research` | Multi-hop research across indexed docs with citations |
| `code-assistant` | Agent with code execution, file I/O, and shell access |
| `scheduled-monitor` | Stateful agent on a schedule with memory |
| `chat-simple` | Lightweight conversation, no tools |

Example:

```bash
jarvis init --preset morning-digest-mac --force
jarvis connect gdrive          # one OAuth covers Gmail / Calendar / Tasks
jarvis digest --fresh          # generate and play your first briefing
```

Per-preset deep dives: [morning digest](https://locus.github.io/Locus/user-guide/morning-digest/) · [deep research](https://locus.github.io/Locus/user-guide/deep-research/) · [code assistant](https://locus.github.io/Locus/user-guide/code-assistant/) · [scheduled monitor](https://locus.github.io/Locus/user-guide/scheduled-monitor/) · [chat simple](https://locus.github.io/Locus/user-guide/chat-simple/) · or the full [quickstart guide](https://locus.github.io/Locus/getting-started/quickstart/).

### Skills

Skills teach agents how to better use tools and improve their reasoning. Every skill is a tool — agents discover them from a catalog and invoke them on demand.

```bash
# Install skills from public sources
jarvis skill install hermes:arxiv
jarvis skill sync hermes --category research

# Use skills with any agent
jarvis ask "Use the code-explainer skill to explain this Python code: for i in range(5): print(i*2)"

# Optimize skills from your trace history
jarvis optimize skills --policy dspy

# Benchmark the impact
jarvis bench skills --max-samples 5 --seeds 42
```

Import from [Hermes Agent](https://github.com/NousResearch/hermes-agent) (~150 skills), [OpenClaw](https://github.com/openclaw/skills) (~13,700 community skills), or any GitHub repo. Skills follow the [agentskills.io](https://agentskills.io/specification) open standard.

See the [Skills User Guide](https://locus.github.io/Locus/user-guide/skills/) and [Skills Tutorial](https://locus.github.io/Locus/tutorials/skills-workflow/) for details.

### Built-in Agents

Locus ships with eight built-in agents across three execution modes (on-demand, scheduled, continuous):

| Agent | Type | What it does |
|-------|------|-------------|
| `morning_digest` | Scheduled | Daily briefing from email, calendar, health, news — with TTS audio |
| `deep_research` | On-demand | Multi-hop research with citations across web and local docs |
| `monitor_operative` | Continuous | Long-horizon monitoring with memory, compression, and retrieval |
| `orchestrator` | On-demand | Multi-turn reasoning with automatic tool selection |
| `native_react` | On-demand | ReAct (Thought-Action-Observation) loop agent |
| `operative` | Continuous | Persistent autonomous agent with state management |
| `native_openhands` | On-demand | CodeAct — generates and executes Python code |
| `simple` | On-demand | Single-turn chat, no tools |

See the [User Guide](https://locus.github.io/Locus/user-guide/morning-digest/) and [Tutorials](https://locus.github.io/Locus/tutorials/) for detailed setup instructions.

Full documentation — including Docker deployment, cloud engines, development setup, and tutorials — at **[locus.github.io/Locus](https://locus.github.io/Locus/)**.

## Community

- **GitHub:** [github.com/locus/Locus](https://github.com/locus/Locus)
- **Discord:** [discord.gg/CMVBmDQ5Fj](https://discord.gg/CMVBmDQ5Fj)
- **X / Twitter:** [@LocusAI](https://x.com/LocusAI)
- **Docs:** [locus.github.io/Locus](https://locus.github.io/Locus/)

## Contributing

We welcome contributions! See the [Contributing Guide](CONTRIBUTING.md) for incentives, contribution types, and the PR process.

Quick start for contributors:

```bash
git clone https://github.com/locus/Locus.git
cd Locus
uv sync --extra dev
uv run pre-commit install
uv run pytest tests/ -v
```

Browse the [Roadmap](https://locus.github.io/Locus/development/roadmap/) for areas where help is needed. Comment **"take"** on any issue to get auto-assigned.

## About

Locus is part of [Intelligence Per Watt](https://www.intelligence-per-watt.ai/), a research initiative studying the intelligence efficiency of AI systems. The project is developed at [Hazy Research](https://hazyresearch.stanford.edu/) and the [Scaling Intelligence Lab](https://scalingintelligence.stanford.edu/) at [Stanford SAIL](https://ai.stanford.edu/).

## Sponsors

<p>
  <a href="https://www.laude.org/">Laude Institute</a> &bull;
  <a href="https://datascience.stanford.edu/marlowe">Stanford Marlowe</a> &bull;
  <a href="https://cloud.google.com/">Google Cloud Platform</a> &bull;
  <a href="https://lambda.ai/">Lambda Labs</a> &bull;
  <a href="https://ollama.com/">Ollama</a> &bull;
  <a href="https://research.ibm.com/">IBM Research</a> &bull;
  <a href="https://hai.stanford.edu/">Stanford HAI</a>
</p>

## Citation
```bibtex
@misc{saadfalcon2026locuspersonalaipersonal,
      title={Locus: Personal AI, On Personal Devices}, 
      author={Jon Saad-Falcon and Avanika Narayan and Robby Manihani and Tanvir Bhathal and Herumb Shandilya and Hakki Orhun Akengin and Gabriel Bo and Andrew Park and Matthew Hart and Caia Costello and Chuan Li and Christopher Ré and Azalia Mirhoseini},
      year={2026},
      eprint={2605.17172},
      archivePrefix={arXiv},
      primaryClass={cs.LG},
      url={https://arxiv.org/abs/2605.17172}, 
}
```

## License

[Apache 2.0](LICENSE)
