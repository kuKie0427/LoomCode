from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable
    is_read_only: bool = False
    is_concurrent_safe: bool = False
    enabled: bool = True


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        # M15: serialise mutations to the registry so concurrent
        # background discovery threads (e.g. mcp_manager) cannot race
        # with the agent loop. Reads (get/names/handler_for) are not
        # guarded — they iterate a dict and Python's GIL protects against
        # torn pointers; the lock guards the *mutation* surface.
        self._LOCK = threading.Lock()

    def register(self, tool: Tool) -> None:
        with self._LOCK:
            if tool.name in self._tools:
                raise ValueError(f"Tool {tool.name!r} already registered")
            self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        with self._LOCK:
            self._tools.pop(name, None)

    def disable(self, name: str) -> None:
        with self._LOCK:
            tool = self._tools.get(name)
            if tool is not None:
                tool.enabled = False

    def enable(self, name: str) -> None:
        with self._LOCK:
            tool = self._tools.get(name)
            if tool is not None:
                tool.enabled = True

    def is_enabled(self, name: str) -> bool:
        tool = self._tools.get(name)
        return bool(tool and tool.enabled)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools.keys())

    def all(self) -> list[Tool]:
        return [self._tools[n] for n in self.names()]

    def to_anthropic_schema(self) -> list[dict]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in self._tools.values()
            if t.enabled
        ]

    def handler_for(self, name: str) -> Callable | None:
        tool = self._tools.get(name)
        return tool.handler if (tool and tool.enabled) else None

