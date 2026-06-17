# Context Management

Two-phase compaction in `context.py::Context`. Designed for the Anthropic SDK with `MessageParam` lists.

## Phase 1: microcompact (cheap, runs on `AgentStop`)

- **Goal**: keep message structure, drop tool result content for old rounds.
- **Targets**: results from `bash`, `glob`, `todo_write`, `task` (in `COMPACTABLE_TOOLS`).
- **Preservation**: the most recent `KEEP_RECENT = 6` rounds are untouched.
- **Cost**: O(n) in-place mutation, no LLM call.

## Phase 2: autocompact (expensive, runs before the next LLM call when `should_compact` returns true)

- **Trigger**: `current_tokens >= context_window * THRESHOLD` where `THRESHOLD = 0.85`.
- **Algorithm**:
  1. Find round boundaries (`_find_rounds`)
  2. From the tail, walk backwards until tail token budget is consumed (`_find_tail_cutoff`, budget = `TAIL_TOKEN_BUDGET_PERCENT * context_window`)
  3. Snap the cutoff to a round boundary (`_align_to_round_start`) — never cut mid-round
  4. Head → summarize via LLM with `COMPACT_PROMPT`
  5. Replace head with a single `system-reminder` user message containing the summary
  6. Inject last-known `todo_write` state if it differs from the tail (`_inject_todo_attachment`)
- **Failure fallback**: if the summary LLM call fails, `autocompact` skips compaction. The messages list is left untouched. The outer `try/except` in `autocompact` catches any unexpected exception and logs it without modifying messages. This matches `test_autocompact_llm_failure_skips_compaction`.

## Token Estimation

- **Primary**: `response.usage.input_tokens` from the LLM.
- **Fallback** (`estimate_tokens`): `sum(len(text) for text in messages) // 4` — chars/4 heuristic.

## Tunable constants

Edit `context.py` to tune. All defaults are conservative for DeepSeek-V4 (1M context).

| Constant | Default | Effect |
|---|---|---|
| `KEEP_RECENT` | `6` | Rounds preserved by microcompact |
| `COMPACTABLE_TOOLS` | `{bash, glob, todo_write, task}` | Tools whose results can be cleared |
| `TAIL_TOKEN_BUDGET_PERCENT` | `0.1` | Fraction of context window kept as tail |
| `COMPACT_MAX_OUTPUT_TOKENS` | `8000` | Max output tokens for summary LLM call |
| `Context.THRESHOLD` | `0.85` | Trigger threshold for autocompact |

## Testing

`tests/test_context.py` covers the full surface. As of Phase 0:

- 25 tests pass (update, current_tokens, _find_tail_cutoff, _generate_summary, _inject_todo_attachment, autocompact happy paths)
- 1 test fails: `test_autocompact_llm_failure_skips_compaction` — the LLM-failure fallback path. Marked `blocked` in `feature_list.json`. See that file's `blocker` field for diagnosis.
