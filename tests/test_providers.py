"""Tests for multi-provider translation in providers.py."""

from hermitclaw.providers import (
    _translate_tools_for_completions,
    _translate_input_to_messages,
    _normalize_completions_response,
)


def test_translate_tools_filters_web_search():
    """web_search_preview should be dropped for Chat Completions providers."""
    tools = [
        {"type": "function", "name": "shell", "description": "Run a command",
         "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
        {"type": "web_search_preview"},
        {"type": "function", "name": "respond", "description": "Talk",
         "parameters": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}},
    ]
    result = _translate_tools_for_completions(tools)
    assert len(result) == 2
    assert all(t["type"] == "function" for t in result)
    assert result[0]["function"]["name"] == "shell"
    assert result[1]["function"]["name"] == "respond"


def test_translate_tools_converts_format():
    """Function tools should be converted to Chat Completions format."""
    tools = [
        {"type": "function", "name": "shell", "description": "Run a command",
         "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    ]
    result = _translate_tools_for_completions(tools)
    assert result[0] == {
        "type": "function",
        "function": {
            "name": "shell",
            "description": "Run a command",
            "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
        },
    }


def test_translate_role_messages_pass_through():
    """Standard role-based messages should pass through unchanged."""
    input_list = [
        {"role": "assistant", "content": "I'm thinking..."},
        {"role": "user", "content": "Hello there"},
    ]
    messages = _translate_input_to_messages(input_list, instructions=None)
    assert messages == input_list


def test_translate_prepends_system_message():
    """Instructions should become a system message at the front."""
    input_list = [{"role": "user", "content": "Hello"}]
    messages = _translate_input_to_messages(input_list, instructions="You are a crab.")
    assert messages[0] == {"role": "system", "content": "You are a crab."}
    assert messages[1] == {"role": "user", "content": "Hello"}


def test_translate_function_call_output():
    """function_call_output items should become tool role messages."""
    input_list = [
        {"role": "user", "content": "Run ls"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_123", "type": "function", "function": {"name": "shell", "arguments": '{"command": "ls"}'}}
        ]},
        {"type": "function_call_output", "call_id": "call_123", "output": "file1.txt\nfile2.txt"},
    ]
    messages = _translate_input_to_messages(input_list, instructions=None)
    assert messages[-1] == {"role": "tool", "tool_call_id": "call_123", "content": "file1.txt\nfile2.txt"}


def test_translate_multimodal_content():
    """Responses API image format should be converted to Chat Completions format."""
    input_list = [
        {"role": "user", "content": [
            {"type": "input_image", "image_url": "data:image/png;base64,abc123"},
            {"type": "input_text", "text": "What do you see?"},
        ]},
    ]
    messages = _translate_input_to_messages(input_list, instructions=None)
    assert messages[0]["role"] == "user"
    content = messages[0]["content"]
    assert content[0] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}}
    assert content[1] == {"type": "text", "text": "What do you see?"}


def test_translate_skips_responses_api_objects():
    """Items that are Responses API SDK objects (not dicts) should be skipped."""
    class FakeResponseItem:
        type = "message"
    input_list = [
        {"role": "user", "content": "Hello"},
        FakeResponseItem(),  # SDK object from response["output"]
    ]
    messages = _translate_input_to_messages(input_list, instructions=None)
    assert len(messages) == 1
    assert messages[0]["role"] == "user"


def test_normalize_text_only_response():
    """A text-only response should have text set and empty tool_calls."""
    class FakeContent:
        def __init__(self):
            self.content = "I'm thinking about shells."
            self.tool_calls = None
    class FakeChoice:
        def __init__(self):
            self.message = FakeContent()
    class FakeResponse:
        def __init__(self):
            self.choices = [FakeChoice()]

    result = _normalize_completions_response(FakeResponse())
    assert result["text"] == "I'm thinking about shells."
    assert result["tool_calls"] == []
    assert result["output"] == []


def test_normalize_tool_call_response():
    """Tool calls should be normalized to same format as Responses API."""
    class FakeFunction:
        def __init__(self):
            self.name = "shell"
            self.arguments = '{"command": "ls"}'
    class FakeToolCall:
        def __init__(self):
            self.id = "call_abc"
            self.type = "function"
            self.function = FakeFunction()
    class FakeContent:
        def __init__(self):
            self.content = None
            self.tool_calls = [FakeToolCall()]
    class FakeChoice:
        def __init__(self):
            self.message = FakeContent()
    class FakeResponse:
        def __init__(self):
            self.choices = [FakeChoice()]

    result = _normalize_completions_response(FakeResponse())
    assert result["text"] is None
    assert len(result["tool_calls"]) == 1
    tc = result["tool_calls"][0]
    assert tc["name"] == "shell"
    assert tc["arguments"] == {"command": "ls"}
    assert tc["call_id"] == "call_abc"


def test_normalize_output_for_tool_loop():
    """output list should contain dicts that brain.py can append to input_list.

    When brain.py does input_list += response["output"], the resulting items
    must be translatable by _translate_input_to_messages on the next call.
    """
    class FakeFunction:
        def __init__(self):
            self.name = "shell"
            self.arguments = '{"command": "ls"}'
    class FakeToolCall:
        def __init__(self):
            self.id = "call_abc"
            self.type = "function"
            self.function = FakeFunction()
    class FakeContent:
        def __init__(self):
            self.content = "Let me check."
            self.tool_calls = [FakeToolCall()]
    class FakeChoice:
        def __init__(self):
            self.message = FakeContent()
    class FakeResponse:
        def __init__(self):
            self.choices = [FakeChoice()]

    result = _normalize_completions_response(FakeResponse())
    assert len(result["output"]) == 1
    msg = result["output"][0]
    assert msg["role"] == "assistant"
    assert msg["content"] == "Let me check."
    assert msg["tool_calls"][0]["id"] == "call_abc"
