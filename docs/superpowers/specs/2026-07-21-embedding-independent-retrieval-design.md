# Embedding-independent retrieval with strict grounding

**Date:** 2026-07-21
**Status:** Approved
**Sequence:** Implement FIRST (before the staged-experience / tagging sub-project).

## Problem

RAG/chat cannot answer any corpus question. The Azure `text-embedding-3-small`
deployment 404s, so:

- `bi_encoder.recall_topk` calls `embed_one(query)` → raises → `retrieve()` raises
  → `data_agent._question_flow` catches it, sets `articles = []`.
- `require_citations=True` on the RAG route + zero citations → the output guardrail
  fails-closed → **every** corpus question is refused, including answerable ones.
- The same Azure deployment builds the `article_embeddings` rows, so the vector table
  is empty anyway — the vector path is dead end-to-end without that deployment.

The system is already fail-closed (it refuses rather than hallucinating). The defect is
that it refuses *everything*.

## Goal

Make retrieval work with **no external embedding dependency**, WITHOUT introducing any
scope for hallucination. Answers stay grounded strictly in real retrieved articles with
citations; when nothing sufficiently relevant is found, the chat **refuses and suggests
rephrasing** (shows the closest headlines it did find).

## Non-goals

- Replacing Azure embeddings permanently (that path re-enables automatically when the
  deployment exists).
- A local embedding model (fastembed/bge) — heavier, needs re-embedding + a migration;
  deferred as a possible future enhancement.

## Design

### 1. `app/retrieval/lexical.py` (new)

`recall_topk_lexical(*, project_id, query, k=50, session_id=None, approved_only=True,
section=None, sentiment=None, date_from=None, date_to=None, monitoring_audience=False)
-> list[RecallHit]` — returns the **same `RecallHit` dataclass** the vector path returns.

- Reads approved articles from the `tagged_file` JSONB artifact (source of truth; always
  present). Resolves the session(s) for the project when `session_id` is not given.
- Honors the same filters (approved / monitoring / session / section / sentiment / date).
- Scores with a **BM25-lite** ranking over tokenized `title` + `content` (title boosted),
  IDF computed over the in-memory corpus. Pure Python; no DB vector column, no Azure.
- Returns top-`k` candidates as `RecallHit` (similarity = normalized lexical score).

Single purpose, testable in isolation against a fixed corpus.

### 2. `app/llm_gateway/embeddings.py` — `embeddings_available()` (new)

Cached probe: attempt one tiny embed at first call; TTL-cache the result; flip to
unavailable on the first 404 so we never pay a failing Azure round-trip per query, and so
the system auto-heals to the vector path the moment the deployment appears.

### 3. `app/retrieval/retriever.py` — transparent fallback + relevance floor

`retrieve()` returns `(articles, closest_below_floor)`:

1. If `embeddings_available()` and the vector table has rows for this project →
   `recall_topk` (vector). Else → `recall_topk_lexical`.
2. **Same FlashRank rerank** runs on whichever candidate set (local ONNX cross-encoder,
   no Azure) → ranked list with real relevance scores.
3. **Relevance floor** `RETRIEVAL_MIN_RERANK` (setting; conservative default): keep only
   articles at/above the floor. Everything below the floor, top 2–3, becomes
   `closest_below_floor` (for the rephrase hint).
4. If nothing clears the floor → `articles = []`, `closest_below_floor` populated.

Callers that don't need the second value ignore it. This *tightens* grounding: the model
is never handed barely-relevant articles it could pattern-match into a fake-grounded
answer — a floor the current vector path lacks.

### 4. `app/agents/data_agent.py` — refuse + rephrase hint

In `_question_flow`, the RAG branch calls the new `retrieve()`:

- If above-floor articles exist → unchanged (context block + citations + answer LLM with
  `require_citations`).
- If **zero** above-floor articles → emit `retrieval` event `count: 0` with the closest
  headlines, then **short-circuit to a deterministic refusal** without calling the answer
  LLM at all: *"I don't have coverage on that in the analyzed **{brand}** articles. The
  closest I have is: …"*. No LLM path ⇒ no invention.

### 5. Grounding guarantees (unchanged or tightened)

- `to_context_block`: "answer STRICTLY from these and cite by id."
- `require_citations` stays on for the RAG route; output guardrail stays fail-closed.
- New relevance floor + the deterministic no-match refusal are net-new safeguards.

## Testing

- **Unit (lexical):** an obviously-relevant doc ranks first; an off-topic query yields an
  empty above-floor set (floor drops it).
- **Integration:** with embeddings forced unavailable, a real corpus question returns
  cited articles; an off-topic question refuses and lists the closest headlines.
- **Guardrail:** confirm no answer-LLM call happens on the empty-corpus path.

## Rollback

Setting `RETRIEVAL_MIN_RERANK=0` disables the floor; removing `lexical.py` from the
fallback restores vector-only behavior. No schema/migration changes, so rollback is code-only.
