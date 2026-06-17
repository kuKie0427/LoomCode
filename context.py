from typing import cast

from anthropic import Anthropic
from anthropic.types import MessageParam, ToolResultBlockParam, ToolUseBlockParam
from loguru import logger

KEEP_RECENT = 6
COMPACTABLE_TOOLS = {"bash", "glob", "todo_write", "task"}
TAIL_TOKEN_BUDGET_PERCENT = 0.1  # 尾巴保留 token 预算比例
COMPACT_MAX_OUTPUT_TOKENS = 8000  # 压缩摘要最大输出

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
        """微压缩：保留结构，删减内容，优先保留最新消息""" 
        # Step 1: 找轮边界 — 用 user 消息 + str content 标记每轮起点
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
        """
        自动压缩：总结旧消息，保留最近的消息尾巴。
        调用时机：should_compact() 返回 True 时，在下一轮 API 调用前。
        """
        try:
            # Step 1: 找轮边界
            rounds = self._find_rounds(messages)
            if len(rounds) <= 1:
                return  # 至少 1 轮才压缩

            # Step 2: 从尾部向前找尾巴
            tail_cutoff = self._find_tail_cutoff(messages, int(TAIL_TOKEN_BUDGET_PERCENT * context_window))

            # Step 3: 尾巴里不能截断轮——找到尾巴起始位置所属的轮的起点
            tail_start = self._align_to_round_start(rounds, tail_cutoff)

            # Step 4: 提取要压缩的头部消息 + 尾巴消息
            head_messages = messages[:tail_start]
            tail_messages = messages[tail_start:]

            # Step 5: 提取最后一次 todo 状态（从头部中找）
            last_todo = self._extract_last_todo(head_messages)

            # Step 6: 调用 LLM 生成摘要
            summary = self._generate_summary(head_messages, client, model)
            if not summary:
                logger.warning("压缩摘要生成失败，跳过压缩（caller 应处理 context overflow）")
                return

            # Step 7: 重建 messages 列表
            messages.clear()
            messages.append({
                "role": "user",
                "content": f"<system-reminder>\n对话历史已被压缩。以下是摘要：\n\n{summary}\n</system-reminder>"
            })
            messages.extend(tail_messages)

            # Step 8: 注入 todo 附件（如果头部的 todo 和尾巴里的不一致）
            if last_todo:
                self._inject_todo_attachment(messages, last_todo)

            # Step 9: 重置 token 追踪状态
            self.last_input_tokens = 0
            self.checked_at_index = 0

            logger.success(f"压缩完成：{len(head_messages)} 条消息 → 1 条摘要，保留 {len(tail_messages)} 条尾巴")
        except Exception as e:
            logger.error(f"压缩失败：{e}")

    # ─── 4. 辅助方法 ───

    def _find_rounds(self, messages: list[MessageParam]) -> list[int]:
        """返回每轮起点的索引列表"""
        return [
            i for i, msg in enumerate(messages)
            if msg["role"] == "user" and isinstance(msg["content"], str)
        ]

    def _find_tail_cutoff(self, messages: list[MessageParam], budget: int) -> int:
        """
        从消息列表的末尾向前扫描，保留不超过 budget 个 token。
        返回尾巴起始索引。
        """
        accumulated = 0
        for i in range(len(messages) - 1, -1, -1):
            accumulated += len(self._extract_text(messages[i]["content"])) // 4
            if accumulated >= budget:
                return i
        return 0

    def _align_to_round_start(self, rounds: list[int], cutoff: int) -> int:
        """确保 cutoff 落在某一轮的起点，不能从轮中间截断"""
        for r in reversed(rounds):
            if r <= cutoff:
                return r
        return cutoff

    def _extract_last_todo(self, messages: list[MessageParam]) -> str | None:
        """从消息中提取最后一次 todo_write 的结果"""
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
        """
        调用 LLM 生成摘要。
        用独立消息 + 禁用工具，类似 spawn_subagent 但更轻量。
        """
        try:
            # 格式化旧消息为文本（简化版，生产环境可以更精细）
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
        """在消息列表中插入 todo 附件"""
        messages.insert(1, {
            "role": "user",
            "content": f"<system-reminder>\n压缩前的待办状态：\n{todo_text}\n</system-reminder>"
        })

    def update(self, messages_count: int, response) -> None:
        self.last_input_tokens = response.usage.input_tokens
        self.checked_at_index = messages_count

    def estimate_tokens(self, messages: list[MessageParam]) -> int:
        """fallback: chars/4"""
        return sum(len(self._extract_text(msg["content"])) for msg in messages) // 4

    def current_tokens(self, messages: list[MessageParam]) -> int:
        """当前上下文 token 估计"""
        new_messages = messages[self.checked_at_index:]
        delta = self.estimate_tokens(new_messages)
        return self.last_input_tokens + delta

    def should_compact(self, messages: list[MessageParam], context_window: int) -> bool:
        return self.current_tokens(messages) >= context_window * 0.85
    
    def _extract_text(self, content) -> str:
        """Extract text from message content blocks."""
        if not isinstance(content, list):
            return str(content)
        texts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    texts.append(b.get("text", ""))
            else:
                # SDK ContentBlock 对象（如 response.content）
                if getattr(b, "type", None) == "text":
                    texts.append(getattr(b, "text", ""))
        return "".join(texts)



if __name__ == "__main__":
    ctx = Context()
    # 每轮结构：user(str) → assistant([tool_use, text]) → user([tool_result, text]) → assistant([text])
    # 无工具的轮：user(str) → assistant([text])
    messages: list[MessageParam] = cast(list[MessageParam], [
        # ── 第 1 轮：有工具调用，应被压缩 ──
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

        # ── 第 2 轮：有工具调用，不应被压缩（在 cutoff 之后）──
        {"role": "user", "content": "第二轮"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "2", "name": "glob", "input": {"pattern": "*.py"}},
            {"type": "text", "text": "正在查找..."},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "2", "content": "context.py\nmain.py\nhook.py\nprompt.py"},
            {"type": "text", "text": "找到了这些文件"},
        ]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "完成第二轮"},
        ]},

        # ── 第 3 轮：todo_write，不应被压缩 ──
        {"role": "user", "content": "第三轮"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "3", "name": "todo_write", "input": {"task": "写单元测试"}},
            {"type": "text", "text": "正在写 todo..."},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "3", "content": "已创建任务"},
            {"type": "text", "text": "todo 已更新"},
        ]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "完成第三轮"},
        ]},

        # ── 第 4 轮：task，不应被压缩 ──
        {"role": "user", "content": "第四轮"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "4", "name": "task", "input": {"task": "优化性能"}},
            {"type": "text", "text": "正在创建任务..."},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "4", "content": "task-123"},
            {"type": "text", "text": "任务已创建"},
        ]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "完成第四轮"},
        ]},

        # ── 第 5 轮：无工具 ──
        {"role": "user", "content": "第五轮"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "这是第五轮的回复"},
        ]},

        # ── 第 6 轮：无工具 ──
        {"role": "user", "content": "第六轮"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "这是第六轮的回复"},
        ]},

        # ── 第 7 轮：无工具 ──
        {"role": "user", "content": "第七轮"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "这是第七轮的回复"},
        ]},
    ])
