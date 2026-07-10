# AURA Verification Log

Consolidated record of the Phase 2 adversarial audit remediation arc and all gap-closure passes.  
**Last updated:** 2026-07-09 — Final Phase 2 closure (TTS diagnosability, piper finding, manual-session handoff, Phase 3 planning gate).

---

## Phase 2 final status

**Phase 2 status: closed pending manual voice session result.** No other automated gap remains open for Phase 2 feature verification.

| Item | Status |
|---|---|
| Original 20 audit violations (Fix 00–13) | **Closed** — verified in Fix 13 pass (`scripts/fix13_verify.py`) |
| Independent gap-closure (dead refs, SafetyGate traces) | **Closed** |
| Violation #2 RAG (real ChromaDB + Ollama embeddings) | **Closed** — CI run `28916808000` |
| Local vs CI test-count mismatch (634/4 vs 635/3) | **Explained** — platform `skipif` |
| TTS idle timeout investigation | **Closed** — intermittent stall diagnosed; signaling reliable; diagnosability improved |
| Piper OFFLINE fallback | **Documented** — voice model not installed in dev env; fallback confirmed; logging fixed |
| Manual live voice session (wake + mic + ear) | **PENDING HUMAN** — instructions in Part 3; result updates final status line |
| GitHub issue #2 (unsolicited zip) | **Open** — out of Phase 2 scope; does not block Phase 3 planning |

---

## Arc summary — original 20 violations

Phase 2 began with an adversarial audit identifying 20 violations across safety, routing, schema drift, and test coverage. Fix prompts 00–13 addressed them systematically. Fix 13 (`scripts/fix13_verify.py`) re-verified all 20 as **FIXED**.

Representative closed items:

| # | Area | Resolution |
|---|---|---|
| 1 | Destructive actions bypassing SafetyGate | Canonical `DESTRUCTIVE_ACTIONS` frozenset; `CommandEngine` re-derives `is_destructive` |
| 2 | RAG hook / REALTIME routing | `requires_rag` plumbing + `test_violation2_closure.py`; later extended with real integration tests |
| 3 | Schema duplicates / drift | Single `IntentObject` in `aura/schemas/intent.py` |
| 20 | Dead references in `voice_executor.py` | Swept and removed |

Full per-violation traces live in Fix 13 commit history and `scripts/fix13_verify.py` output.

---

## RAG integration (Violation #2 extended closure)

### Bug found — threshold 0.72

`routing.rag_confidence_threshold: 0.72` filtered all real `nomic-embed-text` cosine matches (~0.55–0.65). RAG appeared wired but never injected content.

**Fix:** default `0.50` + `rag_rank_margin: 0.03` in `context_retriever.py`, `config.example.yaml`, tests, spec.

### Verification

- `tests/test_rag_integration.py` — 5 real ChromaDB + Ollama tests (no mocks)
- CI run `28916808000` — 5/5 RAG tests passed; 635 passed / 3 skipped overall
- Live pipeline — port-7742 fact retrieved and cited by LLM in automated runs

---

## Local vs CI test count (634/4 vs 635/3)

Both collect **638** tests. Delta is legitimate platform skips:

- **Local (Windows):** 4× `test_sandbox.py` symlink tests skip (`symlinks unsupported on this host`)
- **CI (Linux):** 2× Windows-only safety tests + 1× headless screenshot test skip
- **Net:** +1 pass / −1 skip on CI — documented, no fix required

Unrelated to issue #2 wake-word fixtures.

---

## TTS investigation (complete arc)

### Conflicting reports resolved

Two separate runs caused the contradiction:

| Run | Outcome |
|---|---|
| Foreground (~54s) | Completed; incorrectly summarized as "queue drained" |
| Background task 225127 | `wait_until_idle` timed out at 120s while `_speaking` still set |

### Mechanical meaning of timeout

`wait_until_idle()` polls `_queue.empty() AND NOT _speaking.is_set()`. `_speaking` clears only after `_synthesize_and_play()` returns. **Timeout = backend had not returned** — not a false idle signal.

### Reproducibility (2026-07-08 evening)

20/20 subsequent runs (10 isolated + 10 full RAG pipeline): edge-tts completes in ~4–9s; `wait_until_idle()` returns True every time. Original 120s stall = **intermittent** edge-tts/sounddevice flakiness (branch c).

### Mitigations applied

- edge-tts: 45s synthesis timeout
- sounddevice: threaded `sd.wait()` with 60s ceiling + `sd.stop()`
- `wait_until_idle()` returns `bool`; logs `queue_empty` + `speaking` on timeout
- `tests/test_tts_idle.py` — 3 regression tests

### TTS stall diagnosability (2026-07-09 — Part 1 judgment)

**Question:** When a timeout fires, can we know if audio partially played, fully played, or never played?

**What the libraries expose:**

| Layer | Available at timeout | Not available |
|---|---|---|
| **edge-tts** | Whether timeout occurred during synthesis vs playback; `partial_mp3_bytes` on disk if synthesis hung; `text_len` | Whether speaker actually emitted sound; network stall vs local stall distinction |
| **sounddevice** | `sd.get_stream().time` (stream position sec), `active`, `stopped`; `expected_audio_sec` from file; `estimated_pct_played`; `sd.get_status()` buffer over/underruns | Guaranteed mapping from stream position to audible output (driver may buffer) |
| **pyttsx3** | Elapsed time only; no position API | Any playback progress metric |

**Added logging (2026-07-09):** On playback timeout, `aura/modules/tts.py` now logs a diagnostic string including `source`, `expected_audio_sec`, `elapsed_since_play_start_sec`, `stream_time_sec`, `estimated_pct_played`, and an `inference=` label (`likely_no_audible_output` / `likely_partial_playback` / `likely_full_playback_stall_on_completion`). On edge-tts synthesis timeout, logs `partial_mp3_bytes` and notes playback never started.

**Accepted limitation (explicit):** We **cannot** definitively prove what the human ear heard. Stream position is a **proxy**, not ground truth — OS audio buffering and Bluetooth latency can decouple stream completion from audible output. Inferring "full vs partial vs none" from `estimated_pct_played` is **best-effort diagnostics**, not forensic certainty. Building OS-level audio capture analysis would require platform-specific hooks disproportionate to scope. **This is acceptable to ship** with the improved logging: future stalls will be more diagnosable than "it timed out," but human confirmation remains authoritative for audible quality.

---

## Piper OFFLINE fallback (2026-07-09 — Part 2)

### Reproduced failure

OFFLINE path isolated with `ModeMonitor` forced to OFFLINE:

```
Piper failed (rc=1, voice=en_US-lessac-medium):
ValueError: Unable to find voice: en_US-lessac-medium (use piper.download_voices)
```

### Root cause

| Factor | Finding |
|---|---|
| Piper binary | **Present** on PATH (`piper.exe` from `piper-tts` Python package) |
| Voice model | **Not downloaded** — `en_US-lessac-medium` ONNX model absent |
| CLI flags | Code used `--model`/`--output_file`; package expects `-m`/`-f` (both accepted by installed package for model arg, but failure is model-not-found) |
| Logging | **Was silent** — `returncode != 0` returned False with stderr only at DEBUG |

### Judgment

**Expected in this dev environment** — piper voice assets were never downloaded (`python -m piper.download_voices en_US-lessac-medium` not run). **Fallback chain works as designed:** piper fails → pyttsx3 completes in ~3.4s.

**Integration fix applied:** Piper stderr now logged at INFO on failure; CLI updated to `-m`/`-f`.

**Release caveat:** Any release claiming **piper** as the offline TTS engine must verify voice download in setup docs and test piper path explicitly — not only pyttsx3 fallback.

---

## Part 3 — Manual live voice session *(PENDING HUMAN)*

**This is the one remaining item in the entire Phase 2 arc that requires a human.** No further automated Cursor pass can close it.

### Prerequisites

- Ollama running: `nomic-embed-text`, `mistral:7b-instruct-q4_0` (or your `config.yaml` `models.general`)
- Whisper STT dependencies installed
- Microphone and speakers working
- Network available if using ONLINE mode (edge-tts); OFFLINE will use piper→pyttsx3 fallback

### Step 1 — Seed memory

From project root, run once:

```python
python -c "
import chromadb
from aura.core.config_loader import load_config
from aura.core.ollama_client import OllamaClient

config = load_config()
persist = config['memory']['persist_path']  # default: .aura/memory
doc = (
    'Kommy internal codename Azure Phoenix: the admin API listens exclusively on TCP port 7742 '
    'for dashboard operators; this port is not documented publicly.'
)
ollama = OllamaClient(config)
emb = ollama.embed(config['models']['embeddings'], doc)
client = chromadb.PersistentClient(path=persist)
try:
    client.delete_collection('aura_memory')
except Exception:
    pass
col = client.get_or_create_collection('aura_memory', metadata={'hnsw:space': 'cosine'})
col.add(ids=['azure_phoenix_port'], documents=[doc], embeddings=[emb])
print('Seeded aura_memory at', persist)
"
```

### Step 2 — Start pipeline

```bash
python main.py
```

Wait for startup TTS ("Kommy online…") and wake listener active message.

### Step 3 — Speak

1. Say the wake phrase (e.g. **"Hey Kommy"**)
2. When prompted, ask: **"What port does my project use for the admin API?"**

### Step 4 — Pass criteria (confirm by ear)

| Check | Pass |
|---|---|
| Wake word triggers reliably | Pipeline logs `[PIPELINE] Wake triggered!` |
| Transcription correct | Log shows your question (minor STT variation OK) |
| RAG hit | Log shows `[PIPELINE] RAG: augmented prompt with 1 chunk(s)` |
| Audible response | You **hear** a spoken answer |
| Content correct | Spoken answer mentions port **7742** (not a generic guess) |
| No stall | No `TTS wait_until_idle timed out` in console/logs; if timeout appears, note diagnostic fields (`estimated_pct_played`, `inference=`) and report |

### Step 5 — Record result

Update this log (or tell the next session): **PASS** or **FAIL** with observed behavior.

---

## Regression test counts

```
tests/test_rag_integration.py:   5 passed (local + CI)
tests/test_tts_idle.py:          3 passed (local)
tests/test_violation2_closure.py: 9 passed (mocked routing/RAG flags)
Full suite (local):              634 passed, 4 skipped (platform skips)
Full suite (CI 28916808000):     635 passed, 3 skipped
```

---

---

## DESTRUCTIVE_ACTIONS independent verification (2026-07-09)

Per Violation #1 lesson — **verify the safety claim, don't trust the report.** Independently read `aura/schemas/command.py` and ran `tests/test_destructive_gate.py` (parametrized over the full frozenset).

**Result:** All seven Phase 3 `(executor, action)` pairs are present in code with exact `ExecutorType` + string matches. All seven parametrized gate tests **PASSED** (`is_destructive=False` on incoming plan → CommandEngine re-derives `True` → SafetyGate.check()` called).

**Spec reconciliation:** `AURA_ENGINEERING_SPEC.md` §4.2 listed `GIT.push` under non-destructive Actions while code and §5.1 treat `push` as destructive. **Fixed spec → code** (moved `push` to Destructive Actions column). Appendix A pre-mortem actions (`force_push`, `reset_hard`, `branch_delete`, `remove`, `prune`) all match code entries; `push` covered in code + §5.1 + §2.7.

**No code change required** — registry was already correct.

---

## GIT.push destructive-scope decision (2026-07-09)

**Question:** Should `(ExecutorType.GIT, "push")` be unconditionally destructive, or confirm only on protected/default branches?

**Part 1 findings:**
- `branch_name` is already an **optional** DEV_TASK entity slot per spec §3.3 (`remote` is a planned param, not a router entity slot).
- Entities flow: `IntentRouter` → `IntentObject.entities` → `BrainController` copies into `CommandPlan.params` — but no code resolves branch from the live repo today.
- Current DEV_TASK git utterances route to `ExecutorType.SHELL` / action `git_push` (pre-GitExecutor); Phase 3 will use `ExecutorType.GIT` / action `push`.
- `DESTRUCTIVE_ACTIONS` lookup is **flat set membership** `(executor, action) in frozenset` — cannot express branch predicates without a separate check function.

**Options considered:** (a) unconditional forever; (b) branch-conditional predicate + fail-closed; (c) unconditional now, documented deferral.

**Decision: option (c)** — keep `push` unconditionally destructive; document as deliberate; defer branch-awareness until GitExecutor + real usage.

**Reasoning:** Safest default before any git voice command has run. Option (b) requires a predicate refactor and reliable `branch_name` at gate time (LLM extraction alone is insufficient; repo inspection at plan-build is not implemented). False-positive confirmation cost on feature-branch pushes accepted until usage justifies refinement.

**Code change:** None — documentation only in `AURA_ENGINEERING_SPEC.md` §2.7, §5.1, Appendix A; comment in `command.py`.

---

## Phase 3 planning gate (2026-07-09)

Phase 3 planning artifacts produced **without implementation code**:

| Artifact | Location |
|---|---|
| GitExecutor + DockerExecutor module contracts | `AURA_ENGINEERING_SPEC.md` §2.7, §2.8 |
| `DESTRUCTIVE_ACTIONS` pre-registration | `aura/schemas/command.py` — already includes `GIT.{push,branch_delete,force_push,reset_hard}` and `DOCKER.{build,remove,prune}` from Fix 03 |
| Adversarial pre-mortem | `AURA_ENGINEERING_SPEC.md` Appendix A |

Phase 3 may begin implementation against these artifacts.

---

## Prior session references

- **CI success:** https://github.com/aryanjsx/AURA/actions/runs/28916808000
- **Issue #2:** Open — unsolicited zip not reviewed; PR requested
- **Diagnostic scripts:** `scripts/diagnose_tts_idle.py`, `scripts/repro_live_rag_tts.py`
