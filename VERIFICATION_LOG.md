# AURA Verification Log

Consolidated record of adversarial audit remediation and independent gap-closure passes.

---

## 2026-07-08 — Violation #2 RAG retrieval (real integration + deployment gaps)

**RAG retrieval path now verified with real ChromaDB + real embeddings, not mocks. 2026-07-08 session.**

Prior closure of Violation #2 covered flag-plumbing (`requires_rag`), prompt formatting (`augment_prompt_with_rag`), and `main.py` hook wiring only; it did **not** exercise `retrieve_context()` against a live ChromaDB collection with Ollama-produced embedding vectors.

### Environment confirmed (initial pass)

| Dependency | Status |
|---|---|
| `chromadb` | **Was not installed** in project venv; installed `chromadb==1.5.9` for this pass |
| Ollama | Running at `http://localhost:11434` |
| Embeddings model | `nomic-embed-text:latest` pulled and returning 768-dim vectors |

### Bug found and fixed (application code) — threshold

**What was broken:** `routing.rag_confidence_threshold` default of **0.72** filtered out every real match from `nomic-embed-text` cosine similarity. Measured top similarities for correct semantic matches were **0.55–0.65**; with threshold 0.72, `retrieve_context()` always returned `[]` even when ChromaDB was populated.

**User-facing symptom if shipped:** Every `PROJECT_CONTEXT` and `UNKNOWN` voice query would silently proceed **without any retrieved context**, regardless of indexed memory. RAG would appear wired in logs/flags but never inject content.

**Why mocked tests missed it:** `test_violation2_closure.py` patched `retrieve_context()` or passed hardcoded chunk lists; no test called ChromaDB query or applied the real similarity filter.

**Fix (threshold):**
- `aura/memory/context_retriever.py` — default threshold fallback `0.72` → `0.50`
- `config.example.yaml` — `rag_confidence_threshold: 0.50` with calibration comment
- Swept all live config paths (see Part 2 below)

### Bug found and fixed (application code) — rank-2 bleed

**What was observed:** With threshold 0.50 alone, an auth query returned PostgreSQL doc as rank-2 (similarity ~0.55 vs JWT ~0.60). `augment_prompt_with_rag()` includes **every** chunk from `retrieve_context()` — no top-1-only step — so Postgres text would be injected into auth prompts.

**Judgment:** Cross-topic rank-2 bleed **degrades** response quality (database noise on an auth question). Closely related auth-adjacent docs (JWT + session cookies, similarity within ~0.02) are **acceptable** additional context.

**Fix (rank margin):** Added `routing.rag_rank_margin` (default **0.03**). Secondary chunks must be within 0.03 cosine similarity of rank-1. This keeps JWT + session cookies for broad auth queries and drops Postgres/deploy bleed. Documented in `context_retriever.py` and `config.example.yaml`.

### Part 1 — CI / chromadb dependency

| Item | Before | After | Status |
|---|---|---|---|
| `requirements.txt` | `# chromadb` commented out (line 41) | `chromadb==1.5.9` pinned, uncommented | **Fixed** |
| CI installs chromadb | No — commented dep | Yes — `pip install -r requirements.txt` | **Fixed** |
| CI runs `test_rag_integration.py` | No Ollama → tests skipped silently | Ollama + `nomic-embed-text` + `llama3.2:3b` in `.github/workflows/ci.yml` | **Fixed + verified in CI** |
| CI run result | N/A | Run `28916808000` success — 5/5 RAG tests passed | **Verified** |

**CI design:** Real-embedding tests are **not** dev-only. They require Ollama in CI because mocked tests cannot catch the 0.72 threshold regression. `test_rag_integration.py` still `pytest.skip`s if Ollama is unreachable (fail-safe for local runs without Ollama), but CI workflow now provisions Ollama explicitly.

### Part 2 — Threshold sweep (`rag_confidence_threshold` / `0.72`)

| File | Line/context | Category | Action |
|---|---|---|---|
| `requirements.txt` | was `# chromadb` | N/A (dep, not threshold) | Pinned `chromadb==1.5.9` |
| `config.example.yaml` | `rag_confidence_threshold` | (a) already 0.50 | Added `rag_rank_margin: 0.03` + expanded comment |
| `aura/memory/context_retriever.py` | default fallback | (a) already 0.50 | Added `rag_rank_margin` default 0.03 |
| `tests/test_violation2_closure.py` | fixture `routing` | **(b) was 0.72** | **Fixed → 0.50** with comment |
| `tests/test_rag_integration.py` | fixtures | (a) already 0.50 | Added `rag_rank_margin: 0.03` |
| `AURA_ENGINEERING_SPEC.md` | §config routing | **(b) was 0.72** | **Fixed → 0.50** + `rag_rank_margin` + pointer to this log |
| `VERIFICATION_LOG.md` | bug description | **(c) historical** | Keeps 0.72 to describe the bug |
| `config.yaml` | — | absent in repo (gitignored) | No copy in workspace |

### Part 3 — Rank-2 evaluation (extended integration tests)

**Inclusion determination:** `retrieve_context()` returns up to `memory.max_results` chunks passing threshold + rank-margin. `main.py` passes the full list to `augment_prompt_with_rag()`, which joins **all** chunks into the prompt. Rank-2+ **are** included when they pass filters.

**Extended test seed:** 4 docs — `auth_jwt`, `auth_session`, `db` (PostgreSQL), `deploy` (Kubernetes).

**Results (real embeddings, local run):**
- `test_relevant_auth_query_returns_auth_document` — **PASS** (JWT rank-1; Postgres/deploy excluded)
- `test_auth_adjacent_documents_both_included_when_closely_ranked` — **PASS** (JWT + session cookies both included)
- `test_augmented_prompt_excludes_postgres_noise_for_auth_query` — **PASS** (traced prompt contains JWT + session text; **no** PostgreSQL string)
- `test_irrelevant_weather_query_returns_empty_below_threshold` — **PASS**
- `test_empty_collection_returns_empty_list_cleanly` — **PASS**

### Part 4 — Three-tier confidence (RAG feature)

| Layer | What | Confidence level |
|---|---|---|
| **Verified live** | `OllamaClient.embed()` → ChromaDB `query()` → threshold + rank-margin filter → `retrieve_context()` chunk list → `augment_prompt_with_rag()` output string with real retrieved content | **Live integration test** (`tests/test_rag_integration.py`) |
| **Verified by code trace (not live-tested)** | Augmented prompt → `main.py` `_stream_to_tts(ollama, tts, model, prompt, ...)` → `ollama.chat_stream()` token loop → `tts.speak(sentence)` per sentence → `tts.wait_until_idle()` | Wiring is real and connected; same pattern used for `GENERAL_KNOWLEDGE` (verified in earlier violation traces with mocks). RAG branch only changes `prompt` before the existing `_stream_to_tts` call at `main.py` lines 240–257. |
| **Not yet verified** | Real microphone STT input → live Ollama LLM latency/quality under voice load → audible TTS output with RAG-augmented content | Requires manual end-to-end voice session |

### Tests added / updated

- **File:** `tests/test_rag_integration.py` (separate from mocked `test_violation2_closure.py`)
- **Class:** `TestRagIntegrationReal` — 5 cases (see Part 3)

### Regression runs

```
tests/test_rag_integration.py: 5 passed (local extended suite)
Full suite (local, this session): 634 passed, 4 skipped
Full suite (CI run 28916808000): 635 passed, 3 skipped
```

### CI verification

**Run 1** (commit `3c487cd`, workflow `28916569324`): **FAILED** overall — but RAG integration tests **PASSED**:
```
tests/test_rag_integration.py .....   [5/5 passed in CI]
```
Failures were **unrelated** to RAG: `test_phase2_audit_part1.py` Ollama `chat()` tests got HTTP 404 because CI pulled only `nomic-embed-text`, not `llama3.2:3b` (`config.models.fast`). Fixed in CI workflow by adding `ollama pull llama3.2:3b`.

**Run 2** (commit `3309404`, workflow `28916808000`): **SUCCESS** — https://github.com/aryanjsx/AURA/actions/runs/28916808000
```
tests/test_rag_integration.py .....   [5/5 passed in CI]
================== 635 passed, 3 skipped in 120.26s ==================
```

---

## Prior entries (summary)

- **Fix 13 / gap-closure (2026-07-08):** Violations #1, #20 closed with per-action SafetyGate traces and `voice_executor.py` dead-reference sweep. Violation #2 closed for routing flags, REALTIME online/offline branches, and mocked RAG plumbing.
