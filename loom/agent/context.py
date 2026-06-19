from typing import cast

from anthropic import Anthropic
from anthropic.types import MessageParam, ToolResultBlockParam, ToolUseBlockParam
from loguru import logger

from loom.agent.config import LLM_CONFIG

KEEP_RECENT = 6
COMPACTABLE_TOOLS = {"bash", "glob", "todo_write", "task"}
TAIL_TOKEN_BUDGET_PERCENT = 0.1
# Backward-compat alias — prefer LLM_CONFIG.max_output_tokens for new code.
# Kept as a module constant so external imports keep working after the P2 refactor.
COMPACT_MAX_OUTPUT_TOKENS = LLM_CONFIG.max_output_tokens

# Default model for accurate token counting when caller doesn't pass one.
# Falls back to a cheap / always-available model name so count_tokens works
# even when the project's primary model differs.
_DEFAULT_MODEL = "claude-haiku-4-5"

# Cache for accurate token counts, keyed by id(messages). Same message-list
# object is not recounted across should_compact / current_tokens calls.
_token_cache: dict[int, int] = {}


def _count_tokens_accurate(messages: list[MessageParam], model: str) -> int:
    """Call anthropic.messages.count_tokens(). Returns -1 on any failure.

    Cached by id(messages) — same list object → no second HTTP roundtrip.
    """
    key = id(messages)
    if key in _token_cache:
        return _token_cache[key]
    try:
        client = Anthropic()
        result = client.messages.count_tokens(model=model, messages=cast(list, messages))
        count = int(result.input_tokens)
    except Exception as e:
        logger.debug("count_tokens failed ({}), falling back to heuristic", e)
        return -1
    _token_cache[key] = count
    return count

COMPACT_PROMPT = """你正在压缩一段对话历史。请阅读以下消息，输出一个结构化摘要。

要求：
- 只输出摘要，不要输出任何问候、解释或对话
- 不要使用工具
- 用简洁的要点列表，不要重放对话原文

按以下格式输出：

## 1. 用户意图
- 用户想达成什么目标
- 用户最新的未完成需求

## 2. 已完成工作
- 改动了哪些文件
- 完成了哪些任务
- 跑过什么验证

## 3. 活跃上下文
- 当前正在编辑的文件
- 关键函数名、变量名、数据结构
- 重要的外部引用或依赖

## 4. 待办任务
- 还有哪些没做完
- 已知的阻塞项

## 5. 显式约束
- 只记录用户明确说过的限制或要求（原话）
- 不要推测、不要添加、不要修改

## 6. 注意事项
- 遇到的 bug、踩的坑
- 重要的上下文信息，压缩后会丢失"""

class Context:
    def __init__(self):
        self.last_input_tokens: int = 0
        self.checked_at_index: int = 0
        self.THRESHOLD = 0.85

    def microcompact(self, event: str, messages: list[MessageParam]) -> None:
        round_indices = [
            i for i, msg in enumerate(messages)
            if msg["role"] == "user" and isinstance(msg["content"], str)
        ]
        if len(round_indices) <= KEEP_RECENT:
            return

        cutoff = round_indices[len(round_indices) - KEEP_RECENT]

        tool_names: dict[str, str] = {}
        for msg in messages:
            if msg["role"] != "assistant":
                continue
            content = msg["content"]
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    t = cast(ToolUseBlockParam, block)
                    tool_names[t["id"]] = t["name"]

        for i in range(cutoff):
            msg = messages[i]
            if msg["role"] != "user":
                continue
            content = msg["content"]
            if not isinstance(content, list):
                continue
            for block in content:
                if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                    continue
                tr = cast(ToolResultBlockParam, block)
                tool_use_id = tr["tool_use_id"]
                tool_name = tool_names.get(tool_use_id, "")
                if tool_name in COMPACTABLE_TOOLS:
                    tr["content"] = "[Old tool result content cleared]"
    def autocompact(self, messages: list[MessageParam], client: Anthropic, model: str, context_window: int) -> None:
        try:
            rounds = self._find_rounds(messages)
            if len(rounds) <= 1:
                return

            tail_cutoff = self._find_tail_cutoff(messages, int(TAIL_TOKEN_BUDGET_PERCENT * context_window))

            tail_start = self._align_to_round_start(rounds, tail_cutoff)

            head_messages = messages[:tail_start]
            tail_messages = messages[tail_start:]

            last_todo = self._extract_last_todo(head_messages)

            summary = self._generate_summary(head_messages, client, model)
            if not summary:
                logger.warning("压缩摘要生成失败，跳过压缩（caller 应处理 context overflow）")
                return

            messages.clear()
            messages.append({
                "role": "user",
                "content": f"<system-reminder>\n对话历史已被压缩。以下是摘要：\n\n{summary}\n</system-reminder>"
            })
            messages.extend(tail_messages)

            if last_todo:
                self._inject_todo_attachment(messages, last_todo)

            self.last_input_tokens = 0
            self.checked_at_index = 0

            logger.success(f"压缩完成：{len(head_messages)} 条消息 → 1 条摘要，保留 {len(tail_messages)} 条尾巴")
        except Exception as e:
            logger.error(f"压缩失败：{e}")

    def _find_rounds(self, messages: list[MessageParam]) -> list[int]:
        return [
            i for i, msg in enumerate(messages)
            if msg["role"] == "user" and isinstance(msg["content"], str)
        ]

    def _find_tail_cutoff(self, messages: list[MessageParam], budget: int) -> int:
        accumulated = 0
        for i in range(len(messages) - 1, -1, -1):
            accumulated += len(self._extract_text(messages[i]["content"])) // 4
            if accumulated >= budget:
                return i
        return 0

    def _align_to_round_start(self, rounds: list[int], cutoff: int) -> int:
        for r in reversed(rounds):
            if r <= cutoff:
                return r
        return cutoff

    def _extract_last_todo(self, messages: list[MessageParam]) -> str | None:
        for msg in reversed(messages):
            content = msg["content"]
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_result":
                    continue
                if block.get("is_error", True) is not False:
                    continue
                block_content = block.get("content")
                if isinstance(block_content, str) and block_content.startswith("## Current Tasks"):
                    return block_content

        return None

    def _generate_summary(self, messages: list[MessageParam], client: Anthropic,model: str) -> str | None:
        try:
            conversation = "\n".join(
                f"[{msg['role']}]: {self._extract_text(msg['content'])[:2000]}"
                for msg in messages
            )

            summary_request : list[MessageParam]= [
                {"role": "user", "content": f"{COMPACT_PROMPT}\n\n<conversation>\n{conversation[:50000]}\n</conversation>"}
            ]

            response = client.messages.create(
                model=model,
                system="你是一个对话压缩助手。只输出结构化摘要，不要做任何其他事。",
                messages=summary_request,
                max_tokens=COMPACT_MAX_OUTPUT_TOKENS,
                tools=[],
            )

            return self._extract_text(response.content)

        except Exception as e:
            logger.error(f"压缩摘要生成失败: {e}")
            return None

    def _inject_todo_attachment(self, messages: list[MessageParam], todo_text: str) -> None:
        messages.insert(1, {
            "role": "user",
            "content": f"<system-reminder>\n压缩前的待办状态：\n{todo_text}\n</system-reminder>"
        })

    def update(self, messages_count: int, response) -> None:
        self.last_input_tokens = response.usage.input_tokens
        self.checked_at_index = messages_count

    def estimate_tokens(self, messages: list[MessageParam]) -> int:
        return sum(len(self._extract_text(msg["content"])) for msg in messages) // 4

    def current_tokens(self, messages: list[MessageParam]) -> int:
        new_messages = messages[self.checked_at_index:]
        delta = self.estimate_tokens(new_messages)
        return self.last_input_tokens + delta

    def should_compact(self, messages: list[MessageParam], context_window: int, model: str | None = None) -> bool:
        cheap = self.current_tokens(messages)
        gate = context_window * self.THRESHOLD * 0.9
        if cheap < gate:
            return cheap >= context_window * self.THRESHOLD
        # Cheap says we're close — call real counter for an accurate read.
        # Use max(cheap, accurate) as the safe estimate: better to over-trigger
        # (harmless compaction) than to under-trigger (context overflow).
        accurate = _count_tokens_accurate(messages, model or _DEFAULT_MODEL)
        total = max(cheap, accurate) if accurate > 0 else cheap
        return total >= context_window * self.THRESHOLD

    def _extract_text(self, content) -> str:
        if not isinstance(content, list):
            return str(content)
        texts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    texts.append(b.get("text", ""))
            else:
                if getattr(b, "type", None) == "text":
                    texts.append(getattr(b, "text", ""))
        return "".join(texts)



if __name__ == "__main__":
    ctx = Context()
    messages: list[MessageParam] = cast(list[MessageParam], [
        {"role": "user", "content": "第一轮"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "1", "name": "bash", "input": {"command": "ls"}},
            {"type": "text", "text": "正在执行 ls..."},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "1", "content": "file1.txt\nfile2.txt"},
            {"type": "text", "text": "结果如上"},
        ]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "完成第一轮"},
        ]},
    ])
    ctx.microcompact("PreToolUse", messages)
    print(f"After microcompact: {len(messages)} messages")
    for m in messages:
        print(f"  role={m['role']}: {str(m['content'])[:60]}")
