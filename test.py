from anthropic import Anthropic
from context import Context
from anthropic.types import ToolParam

api_key = "sk-8f1801c3314d4ac597b615cf2a33dac1"
base_url = "https://api.deepseek.com/anthropic"

client = Anthropic(
    api_key=api_key,
    base_url=base_url,
    max_retries=3,
    timeout=60.0,
)

context = Context()
TOOLS:list[ToolParam]= [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to a file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace exact text in a file once.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
    {"name": "glob", "description": "Find files matching a glob pattern.",
     "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}},
    {"name": "todo_write", "description": "Create and manage a task list for your current coding session.",
     "input_schema": {"type": "object", "properties": {"todos": {"type": "array", "items": {"type": "object", "properties": {"content": {"type": "string"}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}}, "required": ["content", "status"]}}}, "required": ["todos"]}},
]
response = client.messages.create(
            model="deepseek-v4-flash", 
            messages=[{"role": "user", "content": "你好"}], 
            max_tokens=8000,
            tools=TOOLS
)


if __name__ == "__main__":
    
    print(response.content)