# Testing

## Test Files

| File | Coverage |
|---|---|
| `tests/test_prompt.py` | `SystemPrompt.add_static/add_dynamic/build`, `get_git_context` |
| `tests/test_models.py` | `LLMClient` init, `change_model`, `get_context_window` |
| `tests/test_hook.py` | `Hooks.register_hook`, `trigger_hooks`, `log_hook` |
| `tests/test_context.py` | `Context.microcompact`, `autocompact`, token estimation, fallback paths |
| `tests/test_tools.py` | All `run_*` tool handlers + `safe_path` |
| `tests/test_agent_loop.py` | `agent_loop` end-to-end with mocked LLM, `spawn_subagent` |

Total: **71 tests** (70 pass, 1 blocked — see `feature_list.json::f-test-framework-p4`).

## Fixtures (`conftest.py`)

- `temp_workdir` — `tmp_path / "workdir"` for filesystem tests. Never touch the real filesystem.
- `sample_messages` — three complete rounds of conversation as `MessageParam` objects.
- `mock_anthropic_client` — fully mocked `Anthropic` client. `messages.create` returns a pre-configured response with known content + usage. **No real API calls.**

## Running Tests

```bash
uv run pytest -v                       # all
uv run pytest tests/test_hook.py       # one file
uv run pytest -k "test_microcompact"   # by name
uv run pytest -x                       # stop on first failure
```

## Adding a New Test

1. Use `temp_workdir` instead of touching the real filesystem.
2. Use `mock_anthropic_client` (or `mocker.patch`) instead of real LLM calls.
3. Place in the file matching the module under test.
4. Name: `test_<unit>_<scenario>` or `Test<Class>::test_<scenario>` for grouping.
5. After adding, run `./init.sh` to confirm ruff + mypy + pytest all stay green.

## When a Test Fails

1. Read the assertion and trace backwards to the production code.
2. Check `feature_list.json` — if the feature is `blocked`, the test failure is expected; document the diagnosis in the `blocker` field.
3. If the failure is in a `done` feature, the regression is real. Revert or fix.
4. If the failure repeats 3+ times, update `progress.md` with diagnosis, mark feature `blocked`, ask user.
