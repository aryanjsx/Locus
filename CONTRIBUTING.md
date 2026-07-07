# 🤝 Contributing to Kommy (AURA)

First off — thank you for considering contributing. Whether it's fixing a typo, proposing a feature, or building an entire module, every contribution moves the project forward.

**Kommy** is the voice assistant users talk to. **AURA** is the offline architecture underneath. The repo is tagged on GitHub for discoverability — if you found us via `voice-assistant`, `ollama`, `whisper`, `local-llm`, or similar topics, jump to [Contribute by topic](#contribute-by-topic) below.

AURA is a fully offline AI developer assistant. We value clean code, clear documentation, and a welcoming community.

---

## Getting Started

### 1. Fork and Clone

```bash
git clone https://github.com/<your-username>/AURA.git
cd AURA
pip install -r requirements.txt
```

### 2. Create a Branch

Always branch from `main`. Use the naming conventions below.

```bash
git checkout -b feat/your-feature-name
```

### 3. Make Your Changes

- Keep each module independent — no cross-dependencies between modules
- Add docstrings to all public functions
- Include error handling
- Test locally (offline — no network calls)

### 4. Commit Your Changes

We follow [Conventional Commits](https://www.conventionalcommits.org/).

**Format:** `type(module): short description`

```
feat(core): add LLM backend abstraction
fix(runtime): resolve path traversal edge case in the router
docs(readme): update architecture diagram
chore(.github): add PR template
test(modules): add unit tests for LLM brain
refactor(runtime): simplify dispatcher routing
```

**Types:** `feat` · `fix` · `docs` · `chore` · `test` · `refactor` · `style`

### 5. Push and Open a PR

```bash
git push origin feat/your-feature-name
```

Then open a Pull Request against `main`. Fill out the [PR template](.github/PULL_REQUEST_TEMPLATE.md) — it takes 30 seconds and helps reviewers understand your change.

---

## Branch Naming Conventions

| Branch Type | Pattern | Example |
|---|---|---|
| Feature | `feat/module-description` | `feat/whisper-stt-integration` |
| Bug fix | `fix/module-description` | `fix/git-push-auth` |
| Documentation | `docs/description` | `docs/architecture-update` |
| Experiment | `exp/description` | `exp/llama-prompt-tuning` |

---

## Contribute by topic

GitHub topics on this repo mirror real modules. Pick the row that matches your experience:

| Topic | Directory | Good first tasks |
|---|---|---|
| `python`, `open-source` | anywhere | docs, tests, type hints, CI |
| `voice-assistant`, `whisper`, `kommy` | `aura/modules/stt.py`, `wake_word.py` | cross-platform wake-word tests ([#2](https://github.com/aryanjsx/AURA/issues/2)) |
| `ollama`, `local-llm`, `ai` | `aura/core/ollama_client.py`, `llm_brain.py`, `intent_router.py` | router prompts, streaming edge cases |
| `piper-tts`, `offline-ai` | `aura/modules/tts.py` | TTS engine config, failover tests |
| `automation`, `developer-tools` | `aura/executors/`, `plugins/system/` | new allowlisted commands |
| `gitpython` | `plugins/git/` | Phase 3 git plugin |
| `docker-sdk` | `plugins/docker/` | Phase 3 docker plugin |
| `chromadb` | `plugins/memory/` | Phase 6 memory schema |
| `pyqt6` | `aura/gui/` (planned) | Phase 5 dashboard |

**Claim work:** comment on an issue ("I'd like to work on this") or open a PR linked to an issue. Unclaimed ideas welcome via [Feature Request](https://github.com/aryanjsx/AURA/issues/new?template=feature_request.md).

---

## Where to Contribute

Each module has a clear home in the codebase:

| Module | Directory | Description |
|---|---|---|
| System Layer | `core/` | Intent, policy, config, I/O abstractions, LLM backend |
| Runtime | `aura/runtime/` | Router, registry, engine, worker client, planner |
| Security | `aura/security/` | Sandbox, policy, safety gate, audit chain, manifest |
| Worker | `aura/worker/` | Isolated execution subprocess |
| Plugins | `plugins/` | Worker-only executors (file, process, npm, monitor) |
| Dev Tools | `plugins/git/`, `plugins/docker/` | *(Phase 3 — planned)* GitPython, Docker SDK |
| GUI | `aura/gui/` | *(Phase 4 — planned)* PyQt6 dashboard |
| Memory | `aura/memory/` | *(Phase 5 — planned)* ChromaDB vector store |
| Documentation | `docs/` | Architecture docs, guides |

### Currently Accepting Contributions

#### Phase 0 + Phase 1 — Completed (maintenance)
- Additional test coverage for the automation modules
- Documentation improvements
- `.gitignore` and project config refinements
- Bug reports and fixes against the secure-execution core

#### Phase 2 — Voice + Intelligence (maintenance + polish)
- Whisper STT integration and optimization
- Ollama prompt engineering for developer tasks
- Piper TTS voice configuration
- SafetyGate audit coverage ([#3](https://github.com/aryanjsx/AURA/issues/3))
- Cross-platform wake-word and mic tests ([#2](https://github.com/aryanjsx/AURA/issues/2))

#### Phase 3+ — Future (planned)
- Git automation edge cases
- Docker SDK integration
- PyQt6 dashboard components
- ChromaDB memory schema design

---

## Coding Standards

- **Python 3.10+** — use type hints and `from __future__ import annotations`
- **Docstrings** on all public functions (NumPy-style)
- **Error handling** — never let exceptions propagate silently
- **No cloud dependencies** — everything must work fully offline
- **No hardcoded secrets** — use environment variables
- **Modular design** — each file should be independently importable

---

## Reporting Issues

- Use the [Bug Report](https://github.com/aryanjsx/AURA/issues/new?template=bug_report.md) template for bugs
- Use the [Feature Request](https://github.com/aryanjsx/AURA/issues/new?template=feature_request.md) template for ideas
- Use the [Module Proposal](https://github.com/aryanjsx/AURA/issues/new?template=module_proposal.md) template to propose a new AURA module

Want to claim an issue? Comment "I'd like to work on this" and we'll assign it to you.

---

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you agree to uphold a welcoming, respectful, and harassment-free environment.

---

Thank you for helping build the future of offline AI dev tools. ⚡
