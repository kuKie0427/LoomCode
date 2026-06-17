from __future__ import annotations

import inspect
import os
from unittest.mock import MagicMock, patch

from anthropic import AsyncAnthropic
from anthropic.types import (
    InputJSONDelta,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageStartEvent,
    TextBlock,
    ToolUseBlock,
    Usage,
)
from anthropic.types import (
    Message as AnthropicMessage,
)

from loop.agent.llm import LLMClient, StreamEvent
from loop.eval.runner import EvalCase, EvalResult

# ── helpers ──────────────────────────────────────────────────────────────────

def _set_test_api_key() -> str:
    old = os.environ.get("ANTHROPIC_API_KEY", "")
    os.environ["ANTHROPIC_API_KEY"] = "test-key-for-eval"
    return old


def _restore_api_key(old: str) -> None:
    if old:
        os.environ["ANTHROPIC_API_KEY"] = old
    else:
        os.environ.pop("ANTHROPIC_API_KEY", None)



# ── Case 1: LLMClient has async_client ───────────────────────────────────────

class LLMClientHasAsyncClient(EvalCase):
    name = "llm-client-has-async-client"
    description = "LLMClient().async_client is an AsyncAnthropic instance"

    _old_key: str = ""

    def setup(self) -> None:
        self._old_key = _set_test_api_key()

    def teardown(self) -> None:
        _restore_api_key(self._old_key)

    def run(self) -> EvalResult:
        client = LLMClient("test-model")
        if not isinstance(client.async_client, AsyncAnthropic):
            return EvalResult(
                name=self.name, passed=False,
                detail=f"async_client is {type(client.async_client).__name__}, expected AsyncAnthropic",
            )
        return EvalResult(name=self.name, passed=True, detail="async_client is AsyncAnthropic")


# ── Case 2: stream_iter returns generator ─────────────────────────────────────

class LLMClientStreamIterIsGenerator(EvalCase):
    name = "llm-client-stream-iter-is-generator"
    description = "stream_iter() returns a generator object"

    _old_key: str = ""

    def setup(self) -> None:
        self._old_key = _set_test_api_key()

    def teardown(self) -> None:
        _restore_api_key(self._old_key)

    def run(self) -> EvalResult:
        client = LLMClient("test-model")
        result = client.stream_iter("sys", [], [])
        if not inspect.isgenerator(result):
            return EvalResult(
                name=self.name, passed=False,
                detail=f"Expected generator, got {type(result).__name__}",
            )
        return EvalResult(name=self.name, passed=True, detail="stream_iter returns a generator")


# ── Case 3: stream_iter yields StreamEvent ────────────────────────────────────

class LLMClientStreamIterYieldsStreamEvent(EvalCase):
    name = "llm-client-stream-iter-yields-stream-event"
    description = "stream_iter yields StreamEvent instances with kind='text' and kind='usage'"

    _old_key: str = ""

    def setup(self) -> None:
        self._old_key = _set_test_api_key()

    def teardown(self) -> None:
        _restore_api_key(self._old_key)

    def run(self) -> EvalResult:
        mock_events = [
            StreamEvent(kind="text", text="Hello"),
            StreamEvent(kind="text", text=" world"),
            StreamEvent(kind="usage", input_tokens=10, output_tokens=5, stop_reason="end_turn"),
        ]
        with patch("loop.agent.llm.asyncio.run", return_value=mock_events):
            client = LLMClient("test-model")
            events = list(client.stream_iter("sys", [], []))

        has_text = any(e.kind == "text" for e in events)
        has_usage = any(e.kind == "usage" for e in events)
        all_se = all(isinstance(e, StreamEvent) for e in events)

        if not has_text:
            return EvalResult(name=self.name, passed=False, detail="No StreamEvent with kind='text'")
        if not has_usage:
            return EvalResult(name=self.name, passed=False, detail="No StreamEvent with kind='usage'")
        if not all_se:
            return EvalResult(name=self.name, passed=False, detail="Not all items are StreamEvent instances")

        text_count = sum(1 for e in events if e.kind == "text")
        usage_count = sum(1 for e in events if e.kind == "usage")
        return EvalResult(name=self.name, passed=True,
                          detail=f"{len(events)} events: {text_count} text, {usage_count} usage")


# ── Case 4: stream_iter handles tool_use ──────────────────────────────────────

class LLMClientStreamIterHandlesToolUse(EvalCase):
    name = "llm-client-stream-iter-handles-tool-use"
    description = "stream_iter correctly assembles tool_use blocks"

    _old_key: str = ""

    def setup(self) -> None:
        self._old_key = _set_test_api_key()

    def teardown(self) -> None:
        _restore_api_key(self._old_key)

    def run(self) -> EvalResult:
        mock_events = [
            StreamEvent(kind="text", text="Running command"),
            StreamEvent(kind="tool_use", tool_name="bash",
                        tool_input={"command": "echo hi"}, tool_id="tu_1"),
            StreamEvent(kind="usage", input_tokens=10, output_tokens=5, stop_reason="tool_use"),
        ]
        with patch("loop.agent.llm.asyncio.run", return_value=mock_events):
            client = LLMClient("test-model")
            events = list(client.stream_iter("sys", [], []))

        tool_events = [e for e in events if e.kind == "tool_use"]
        if len(tool_events) != 1:
            return EvalResult(name=self.name, passed=False,
                              detail=f"Expected 1 tool_use, got {len(tool_events)}")
        te = tool_events[0]
        if te.tool_name != "bash":
            return EvalResult(name=self.name, passed=False,
                              detail=f"tool_name: {te.tool_name!r}, expected 'bash'")
        if te.tool_input != {"command": "echo hi"}:
            return EvalResult(name=self.name, passed=False,
                              detail=f"tool_input: {te.tool_input!r}, expected {{'command': 'echo hi'}}")
        if te.tool_id != "tu_1":
            return EvalResult(name=self.name, passed=False,
                              detail=f"tool_id: {te.tool_id!r}, expected 'tu_1'")

        return EvalResult(name=self.name, passed=True, detail="tool_use assembled correctly")


# ── Case 4b: stream_iter tolerates malformed tool_use JSON ─────────────────────

class LLMClientStreamIterHandlesMalformedJson(EvalCase):
    name = "llm-client-stream-iter-handles-malformed-json"
    description = "stream_iter falls back to empty input when tool_use JSON is malformed"

    def run(self) -> EvalResult:
        message_start = RawMessageStartEvent(
            type="message_start",
            message=AnthropicMessage(
                id="msg_1", type="message", role="assistant", model="test-model",
                content=[], stop_reason=None, stop_sequence=None,
                usage=Usage(input_tokens=10, output_tokens=0),
            ),
        )
        content_block_start = RawContentBlockStartEvent(
            type="content_block_start",
            index=0,
            content_block=ToolUseBlock(
                type="tool_use", id="tu_1", name="bash", input={},
            ),
        )
        malformed_delta = RawContentBlockDeltaEvent(
            type="content_block_delta",
            index=0,
            delta=InputJSONDelta(
                type="input_json_delta",
                partial_json='{"command": "ls"',
            ),
        )
        content_block_stop = RawContentBlockStopEvent(
            type="content_block_stop", index=0,
        )

        class _AsyncIter:
            def __init__(self, events):
                self._iter = iter(events)
            def __aiter__(self):
                return self
            async def __anext__(self):
                try:
                    return next(self._iter)
                except StopIteration as err:
                    raise StopAsyncIteration from err

        class _StreamCM:
            def __init__(self, events):
                self._events = events
            async def __aenter__(self):
                return _AsyncIter(self._events)
            async def __aexit__(self, *_args):
                return False

        def fake_stream(**_kwargs):
            return _StreamCM([
                message_start, content_block_start,
                malformed_delta, content_block_stop,
            ])

        mock_async = MagicMock()
        mock_async.messages.stream = fake_stream

        client = LLMClient("test-model")
        client.async_client = mock_async

        events = list(client.stream_iter("sys", [], []))

        tool_events = [e for e in events if e.kind == "tool_use"]
        if len(tool_events) != 1:
            return EvalResult(name=self.name, passed=False,
                              detail=f"Expected 1 tool_use, got {len(tool_events)}")
        if tool_events[0].tool_input != {}:
            return EvalResult(name=self.name, passed=False,
                              detail=f"Expected empty dict, got {tool_events[0].tool_input!r}")
        if tool_events[0].tool_id != "tu_1":
            return EvalResult(name=self.name, passed=False,
                              detail=f"tool_id lost: {tool_events[0].tool_id!r}")

        return EvalResult(name=self.name, passed=True,
                          detail="malformed JSON fell back to empty dict without crashing")


# ── Case 5: on_text_delta callback fires per chunk ────────────────────────────

class AgentLoopStreamTextCallbackFiresPerChunk(EvalCase):
    name = "agent-loop-stream-text-callback-fires-per-chunk"
    description = "on_text_delta callback fires once per text chunk in streaming path"

    def run(self) -> EvalResult:
        mock_cb = MagicMock()

        def mock_stream(_sys, _msgs, _tools, _max_tok):
            for _ in range(5):
                yield StreamEvent(kind="text", text="X")
            yield StreamEvent(kind="usage", stop_reason="end_turn")

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.get_context_window.return_value = 128000

        with patch("loop.agent.loop.configure_logging"), \
             patch("loop.agent.loop.trace_mod"), \
             patch("loop.agent.loop.checkpoint"), \
             patch("loop.agent.loop.hooks"), \
             patch("loop.agent.loop.context") as mock_ctx:
            mock_ctx.should_compact.return_value = False
            from loop.agent.loop import agent_loop
            agent_loop(
                [{"role": "user", "content": "hi"}],
                llm_client=mock_llm,
                callbacks={"on_text_delta": mock_cb},
                stream_text=mock_stream,
            )

        if mock_cb.call_count != 5:
            return EvalResult(name=self.name, passed=False,
                              detail=f"on_text_delta called {mock_cb.call_count} times, expected 5")
        for call in mock_cb.call_args_list:
            if call[0][0] != "X":
                return EvalResult(name=self.name, passed=False,
                                  detail=f"on_text_delta called with {call[0][0]!r}, expected 'X'")
        return EvalResult(name=self.name, passed=True,
                          detail="on_text_delta called 5 times with 'X'")


# ── Case 6: streaming path handles tool_use ───────────────────────────────────

class AgentLoopStreamTextHandlesToolUse(EvalCase):
    name = "agent-loop-stream-text-handles-tool-use"
    description = "streaming path triggers tool execution and on_tool_use callback"

    def run(self) -> EvalResult:
        on_tool_use_cb = MagicMock()
        on_text_cb = MagicMock()

        stream_calls = iter([
            iter([
                StreamEvent(kind="text", text="Let me run that"),
                StreamEvent(kind="tool_use", tool_name="bash",
                            tool_input={"command": "ls"}, tool_id="tu_1"),
                StreamEvent(kind="usage", stop_reason="tool_use"),
            ]),
            iter([
                StreamEvent(kind="text", text="Done"),
                StreamEvent(kind="usage", stop_reason="end_turn"),
            ]),
        ])

        def mock_stream(_sys, _msgs, _tools, _max_tok):
            return next(stream_calls)

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.get_context_window.return_value = 128000

        mock_tool_result = [
            {"type": "tool_result", "tool_use_id": "tu_1",
             "content": "file1.py\nfile2.py", "is_error": False},
        ]

        with patch("loop.agent.loop.configure_logging"), \
             patch("loop.agent.loop.trace_mod"), \
             patch("loop.agent.loop.checkpoint"), \
             patch("loop.agent.loop.hooks"), \
             patch("loop.agent.loop.context") as mock_ctx, \
             patch("loop.agent.loop._run_tool_turn", return_value=mock_tool_result):
            mock_ctx.should_compact.return_value = False
            from loop.agent.loop import agent_loop
            agent_loop(
                [{"role": "user", "content": "list files"}],
                llm_client=mock_llm,
                callbacks={"on_tool_use": on_tool_use_cb, "on_text_delta": on_text_cb},
                stream_text=mock_stream,
            )

        if on_tool_use_cb.call_count < 1:
            return EvalResult(name=self.name, passed=False,
                              detail="on_tool_use callback was not called")
        call_args = on_tool_use_cb.call_args[0]
        if call_args[0] != "bash":
            return EvalResult(name=self.name, passed=False,
                              detail=f"on_tool_use name: {call_args[0]!r}, expected 'bash'")
        if call_args[1] != {"command": "ls"}:
            return EvalResult(name=self.name, passed=False,
                              detail=f"on_tool_use input: {call_args[1]!r}")
        if call_args[2] != "tu_1":
            return EvalResult(name=self.name, passed=False,
                              detail=f"on_tool_use id: {call_args[2]!r}, expected 'tu_1'")

        return EvalResult(name=self.name, passed=True,
                          detail="tool_use callback received correct args; tool executed")


# ── Case 7: streaming path uses real token usage ──────────────────────────────

class AgentLoopStreamTextUsesRealTokenUsage(EvalCase):
    name = "agent-loop-stream-text-uses-real-token-usage"
    description = "streaming path passes correct input/output tokens to context.update"

    def run(self) -> EvalResult:
        def mock_stream(_sys, _msgs, _tools, _max_tok):
            yield StreamEvent(kind="text", text="Hi")
            yield StreamEvent(kind="usage", input_tokens=1234, output_tokens=56, stop_reason="end_turn")

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.get_context_window.return_value = 128000

        context_update_spy = MagicMock()

        with patch("loop.agent.loop.configure_logging"), \
             patch("loop.agent.loop.trace_mod"), \
             patch("loop.agent.loop.checkpoint"), \
             patch("loop.agent.loop.hooks"), \
             patch("loop.agent.loop.context") as mock_ctx:
            mock_ctx.should_compact.return_value = False
            mock_ctx.update = context_update_spy
            from loop.agent.loop import agent_loop
            agent_loop(
                [{"role": "user", "content": "hi"}],
                llm_client=mock_llm,
                stream_text=mock_stream,
            )

        if context_update_spy.call_count < 1:
            return EvalResult(name=self.name, passed=False, detail="context.update was not called")
        response = context_update_spy.call_args[0][1]
        usage = response.usage
        if usage.input_tokens != 1234:
            return EvalResult(name=self.name, passed=False,
                              detail=f"input_tokens={usage.input_tokens}, expected 1234")
        if usage.output_tokens != 56:
            return EvalResult(name=self.name, passed=False,
                              detail=f"output_tokens={usage.output_tokens}, expected 56")

        return EvalResult(name=self.name, passed=True,
                          detail=f"context.update received usage: {usage.input_tokens}/{usage.output_tokens} tokens")


# ── Case 8: no stream_text uses sync path ─────────────────────────────────────

class AgentLoopNoStreamTextUsesSyncPath(EvalCase):
    name = "agent-loop-no-stream-text-uses-sync-path"
    description = "agent_loop uses messages.create when stream_text is not provided"

    def run(self) -> EvalResult:
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(type="text", text="Hello")]
        mock_resp.stop_reason = "end_turn"
        mock_resp.usage.input_tokens = 100
        mock_resp.usage.output_tokens = 50

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_resp

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.client = mock_client
        mock_llm.get_context_window.return_value = 128000

        with patch("loop.agent.loop.configure_logging"), \
             patch("loop.agent.loop.trace_mod"), \
             patch("loop.agent.loop.checkpoint"), \
             patch("loop.agent.loop.hooks"), \
             patch("loop.agent.loop.context") as mock_ctx:
            mock_ctx.should_compact.return_value = False
            from loop.agent.loop import agent_loop
            agent_loop(
                [{"role": "user", "content": "hello"}],
                llm_client=mock_llm,
            )

        if mock_client.messages.create.call_count < 1:
            return EvalResult(name=self.name, passed=False, detail="messages.create was not called")
        return EvalResult(name=self.name, passed=True, detail="sync path (messages.create) used")


# ── Case 9: callbacks parameter accepted ──────────────────────────────────────

class AgentLoopAcceptsCallbacksParameter(EvalCase):
    name = "agent-loop-accepts-callbacks-parameter"
    description = "agent_loop accepts callbacks={} without TypeError"

    def run(self) -> EvalResult:
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(type="text", text="ok")]
        mock_resp.stop_reason = "end_turn"
        mock_resp.usage.input_tokens = 100
        mock_resp.usage.output_tokens = 50

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_resp

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.client = mock_client
        mock_llm.get_context_window.return_value = 128000

        try:
            with patch("loop.agent.loop.configure_logging"), \
                 patch("loop.agent.loop.trace_mod"), \
                 patch("loop.agent.loop.checkpoint"), \
                 patch("loop.agent.loop.hooks"), \
                 patch("loop.agent.loop.context") as mock_ctx:
                mock_ctx.should_compact.return_value = False
                from loop.agent.loop import agent_loop
                agent_loop(
                    [{"role": "user", "content": "hi"}],
                    llm_client=mock_llm,
                    callbacks={},
                )
        except TypeError as e:
            return EvalResult(name=self.name, passed=False,
                              detail=f"TypeError raised: {e}")
        return EvalResult(name=self.name, passed=True, detail="callbacks={} accepted")


# ── Case 10: None callbacks default to noop ────────────────────────────────────

class AgentLoopDefaultsCallbacksToNoop(EvalCase):
    name = "agent-loop-defaults-callbacks-to-noop"
    description = "agent_loop with callbacks=None behaves normally (no error, loop completes)"

    def run(self) -> EvalResult:
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(type="text", text="ok")]
        mock_resp.stop_reason = "end_turn"
        mock_resp.usage.input_tokens = 100
        mock_resp.usage.output_tokens = 50

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_resp

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.client = mock_client
        mock_llm.get_context_window.return_value = 128000

        messages = [{"role": "user", "content": "hi"}]
        initial_count = len(messages)

        try:
            with patch("loop.agent.loop.configure_logging"), \
                 patch("loop.agent.loop.trace_mod"), \
                 patch("loop.agent.loop.checkpoint"), \
                 patch("loop.agent.loop.hooks"), \
                 patch("loop.agent.loop.context") as mock_ctx:
                mock_ctx.should_compact.return_value = False
                from loop.agent.loop import agent_loop
                agent_loop(
                    messages,
                    llm_client=mock_llm,
                    callbacks=None,
                )
        except Exception as e:
            return EvalResult(name=self.name, passed=False,
                              detail=f"Exception raised: {type(e).__name__}: {e}")

        if len(messages) <= initial_count:
            return EvalResult(name=self.name, passed=False,
                              detail=f"messages did not grow ({initial_count} → {len(messages)})")
        return EvalResult(name=self.name, passed=True,
                          detail=f"callbacks=None handled; messages grew from {initial_count} to {len(messages)}")


# ── Case 11: on_message_start / on_message_end fire ───────────────────────────

class AgentLoopFiresOnMessageStartAndEnd(EvalCase):
    name = "agent-loop-fires-on-message-start-and-end"
    description = "on_message_start fires once; on_message_end fires with (tool_calls, total_msgs)"

    def run(self) -> EvalResult:
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(type="text", text="Hi")]
        mock_resp.stop_reason = "end_turn"
        mock_resp.usage.input_tokens = 100
        mock_resp.usage.output_tokens = 50

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_resp

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.client = mock_client
        mock_llm.get_context_window.return_value = 128000

        on_start = MagicMock()
        on_end = MagicMock()

        with patch("loop.agent.loop.configure_logging"), \
             patch("loop.agent.loop.trace_mod"), \
             patch("loop.agent.loop.checkpoint"), \
             patch("loop.agent.loop.hooks"), \
             patch("loop.agent.loop.context") as mock_ctx:
            mock_ctx.should_compact.return_value = False
            from loop.agent.loop import agent_loop
            agent_loop(
                [{"role": "user", "content": "hi"}],
                llm_client=mock_llm,
                callbacks={"on_message_start": on_start, "on_message_end": on_end},
            )

        if on_start.call_count != 1:
            return EvalResult(name=self.name, passed=False,
                              detail=f"on_message_start called {on_start.call_count} times, expected 1")
        if on_end.call_count != 1:
            return EvalResult(name=self.name, passed=False,
                              detail=f"on_message_end called {on_end.call_count} times, expected 1")
        if on_end.call_args[0][0] != 0:
            return EvalResult(name=self.name, passed=False,
                              detail=f"on_message_end tool_calls={on_end.call_args[0][0]}, expected 0")

        return EvalResult(name=self.name, passed=True,
                          detail="on_message_start + on_message_end both fired once")


# ── Case 12: on_tool_use / on_tool_result fire ────────────────────────────────

class AgentLoopFiresOnToolUseAndResult(EvalCase):
    name = "agent-loop-fires-on-tool-use-and-result"
    description = "on_tool_use and on_tool_result callbacks fire with correct arguments"

    def run(self) -> EvalResult:
        responses = iter([
            MagicMock(
                content=[
                    ToolUseBlock(type="tool_use", name="bash",
                                 input={"command": "ls"}, id="tu_99"),
                ],
                stop_reason="tool_use",
                usage=MagicMock(input_tokens=100, output_tokens=50),
            ),
            MagicMock(
                content=[TextBlock(type="text", text="Done")],
                stop_reason="end_turn",
                usage=MagicMock(input_tokens=100, output_tokens=50),
            ),
        ])

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = lambda **kw: next(responses)

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.client = mock_client
        mock_llm.get_context_window.return_value = 128000

        on_tool_use = MagicMock()
        on_tool_result = MagicMock()

        with patch("loop.agent.loop.configure_logging"), \
             patch("loop.agent.loop.trace_mod"), \
             patch("loop.agent.loop.checkpoint"), \
             patch("loop.agent.loop.hooks"), \
             patch("loop.agent.loop.context") as mock_ctx:
            mock_ctx.should_compact.return_value = False
            from loop.agent.loop import agent_loop
            agent_loop(
                [{"role": "user", "content": "list files"}],
                llm_client=mock_llm,
                callbacks={"on_tool_use": on_tool_use, "on_tool_result": on_tool_result},
            )

        if on_tool_use.call_count < 1:
            return EvalResult(name=self.name, passed=False, detail="on_tool_use was not called")
        tu_args = on_tool_use.call_args[0]
        if tu_args[0] != "bash":
            return EvalResult(name=self.name, passed=False,
                              detail=f"on_tool_use name: {tu_args[0]!r}, expected 'bash'")
        if tu_args[1] != {"command": "ls"}:
            return EvalResult(name=self.name, passed=False,
                              detail=f"on_tool_use input: {tu_args[1]!r}")
        if tu_args[2] != "tu_99":
            return EvalResult(name=self.name, passed=False,
                              detail=f"on_tool_use id: {tu_args[2]!r}, expected 'tu_99'")

        if on_tool_result.call_count < 1:
            return EvalResult(name=self.name, passed=False, detail="on_tool_result was not called")

        return EvalResult(name=self.name, passed=True,
                          detail="on_tool_use and on_tool_result both fired")


# ── Case 13: on_compact / on_message_end fire ─────────────────────────────────

class AgentLoopFiresOnCompactAndMessageEnd(EvalCase):
    name = "agent-loop-fires-on-compact-and-message-end"
    description = "on_compact called with (before, after); on_message_end with (tool_calls, total_msgs)"

    def run(self) -> EvalResult:
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(type="text", text="Hi")]
        mock_resp.stop_reason = "end_turn"
        mock_resp.usage.input_tokens = 100
        mock_resp.usage.output_tokens = 50

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_resp

        mock_llm = MagicMock()
        mock_llm.model = "test-model"
        mock_llm.client = mock_client
        mock_llm.get_context_window.return_value = 128000

        on_compact = MagicMock()
        on_end = MagicMock()

        with patch("loop.agent.loop.configure_logging"), \
             patch("loop.agent.loop.trace_mod"), \
             patch("loop.agent.loop.checkpoint"), \
             patch("loop.agent.loop.hooks"), \
             patch("loop.agent.loop.context") as mock_ctx:
            mock_ctx.should_compact.side_effect = [True, False]
            mock_ctx.autocompact.side_effect = lambda msgs, *a: msgs.pop()
            mock_ctx.current_tokens.return_value = 100
            from loop.agent.loop import agent_loop
            agent_loop(
                [{"role": "user", "content": "hi"}],
                llm_client=mock_llm,
                callbacks={"on_compact": on_compact, "on_message_end": on_end},
            )

        if on_compact.call_count < 1:
            return EvalResult(name=self.name, passed=False, detail="on_compact was not called")
        c_args = on_compact.call_args[0]
        if len(c_args) < 2:
            return EvalResult(name=self.name, passed=False,
                              detail=f"on_compact args: {c_args}, expected (before, after)")
        if c_args[0] <= c_args[1]:
            return EvalResult(name=self.name, passed=False,
                              detail=f"on_compact: before={c_args[0]}, after={c_args[1]} (expected reduction)")

        if on_end.call_count < 1:
            return EvalResult(name=self.name, passed=False, detail="on_message_end was not called")
        e_args = on_end.call_args[0]
        if len(e_args) < 2:
            return EvalResult(name=self.name, passed=False,
                              detail=f"on_message_end args: {e_args}, expected (tool_calls, total_msgs)")

        return EvalResult(name=self.name, passed=True,
                          detail=f"on_compact({c_args[0]}→{c_args[1]}) + on_message_end({e_args[0]}, {e_args[1]})")
