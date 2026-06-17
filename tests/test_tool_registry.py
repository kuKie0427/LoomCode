from __future__ import annotations

from loop.agent.tool_registry import Tool, ToolRegistry


def hello(args: dict) -> str:
    return f"hello {args.get('name', 'world')}"


def world(args: dict) -> str:
    return "world"


class TestToolRegistry:
    def test_register_and_lookup(self) -> None:
        reg = ToolRegistry()
        reg.register(Tool(name="hello", description="greet", input_schema={}, handler=hello))
        assert reg.get("hello") is not None
        assert reg.handler_for("hello") is hello

    def test_register_duplicate_raises(self) -> None:
        reg = ToolRegistry()
        reg.register(Tool(name="x", description="", input_schema={}, handler=hello))
        with __import__("pytest").raises(ValueError, match="already registered"):
            reg.register(Tool(name="x", description="", input_schema={}, handler=hello))

    def test_disable_and_enable(self) -> None:
        reg = ToolRegistry()
        reg.register(Tool(name="x", description="", input_schema={}, handler=hello))
        assert reg.is_enabled("x")
        reg.disable("x")
        assert not reg.is_enabled("x")
        reg.enable("x")
        assert reg.is_enabled("x")

    def test_disabled_tool_excluded_from_schema(self) -> None:
        reg = ToolRegistry()
        reg.register(Tool(name="a", description="", input_schema={}, handler=hello))
        reg.register(Tool(name="b", description="", input_schema={}, handler=world))
        assert {t["name"] for t in reg.to_anthropic_schema()} == {"a", "b"}
        reg.disable("b")
        assert {t["name"] for t in reg.to_anthropic_schema()} == {"a"}

    def test_disabled_tool_returns_none_handler(self) -> None:
        reg = ToolRegistry()
        reg.register(Tool(name="x", description="", input_schema={}, handler=hello))
        reg.disable("x")
        assert reg.handler_for("x") is None

    def test_names_is_sorted(self) -> None:
        reg = ToolRegistry()
        reg.register(Tool(name="z", description="", input_schema={}, handler=hello))
        reg.register(Tool(name="a", description="", input_schema={}, handler=world))
        reg.register(Tool(name="m", description="", input_schema={}, handler=hello))
        assert reg.names() == ["a", "m", "z"]

    def test_get_unknown_returns_none(self) -> None:
        reg = ToolRegistry()
        assert reg.get("nope") is None

    def test_is_enabled_unknown_returns_false(self) -> None:
        reg = ToolRegistry()
        assert not reg.is_enabled("nope")

    def test_to_anthropic_schema_includes_description_and_schema(self) -> None:
        reg = ToolRegistry()
        reg.register(Tool(
            name="x",
            description="does X",
            input_schema={"type": "object", "properties": {"a": {"type": "string"}}},
            handler=hello,
        ))
        schema = reg.to_anthropic_schema()
        assert len(schema) == 1
        assert schema[0] == {
            "name": "x",
            "description": "does X",
            "input_schema": {"type": "object", "properties": {"a": {"type": "string"}}},
        }

    def test_tool_defaults_read_only_false(self) -> None:
        reg = ToolRegistry()
        reg.register(Tool(name="x", description="", input_schema={}, handler=hello))
        tool = reg.get("x")
        assert tool is not None
        assert tool.is_read_only is False
        assert tool.is_concurrent_safe is False
        assert tool.enabled is True


class TestAgentToolRegistryIntegration:
    def test_loop_tools_loaded_into_registry(self) -> None:
        from loop.agent.tools import TOOL_REGISTRY
        names = TOOL_REGISTRY.names()
        assert "bash" in names
        assert "read_file" in names
        assert "memory_read" in names
        assert "memory_search" in names
        assert "memory_write" in names
        assert "load_skill" in names
        assert "todo_write" in names

    def test_loop_tools_marked_read_only_where_safe(self) -> None:
        from loop.agent.tools import TOOL_REGISTRY
        for name in ("read_file", "memory_read", "memory_search", "glob", "load_skill"):
            tool = TOOL_REGISTRY.get(name)
            assert tool is not None
            assert tool.is_read_only is True, f"{name} should be read-only"
