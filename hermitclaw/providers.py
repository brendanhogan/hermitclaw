"""LLM calls — OpenAI Responses API (default) or Chat Completions (z.ai, etc.)."""

import json
import os
import openai
from hermitclaw.config import config

# If OPENAI_BASE_URL is set, use the Chat Completions API (OpenAI-compatible).
# Leave it unset to use the native OpenAI Responses API.
_BASE_URL = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
_USE_CHAT_COMPLETIONS = bool(_BASE_URL)

# Core tool definitions (Responses API shape; converted for Chat Completions below).
_TOOL_DEFS = [
    {
        "type": "function",
        "name": "shell",
        "description": (
            "Run a shell command inside your environment folder. "
            "You can use ls, cat, mkdir, mv, cp, touch, echo, tee, find, grep, head, tail, wc, etc. "
            "You can also run Python scripts: 'python script.py' or 'python -c \"code\"'. "
            "Use 'cat > file.txt << EOF' or 'echo ... > file.txt' to write files. "
            "Create folders with mkdir. Organize however you like. "
            "All paths are relative to your environment root."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to run"}
            },
            "required": ["command"],
        },
    },
    {
        "type": "function",
        "name": "respond",
        "description": (
            "Talk to your owner! Use this whenever you hear their voice and want to "
            "reply. After you speak, they might say something back — if they do, "
            "use respond AGAIN to keep the conversation going. You can go back and "
            "forth as many times as you like."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "What you say back to them"}
            },
            "required": ["message"],
        },
    },
    {
        "type": "function",
        "name": "move",
        "description": (
            "Move to a location in your room. Use this to go where feels natural "
            "for what you're doing — desk for writing, bookshelf for research, "
            "window for pondering, bed for resting."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "enum": ["desk", "bookshelf", "window", "plant", "bed", "rug", "center"],
                }
            },
            "required": ["location"],
        },
    },
]

# Responses API tool list — includes web_search_preview (OpenAI only).
TOOLS = (
    [{"type": "web_search_preview"}] + _TOOL_DEFS
    if not _USE_CHAT_COMPLETIONS
    else _TOOL_DEFS
)


def _cc_tools() -> list:
    """Convert tool definitions to Chat Completions format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("parameters", {}),
            },
        }
        for t in _TOOL_DEFS
    ]


def _client() -> openai.OpenAI:
    kwargs: dict = {"api_key": config["api_key"]}
    if _BASE_URL:
        kwargs["base_url"] = _BASE_URL
    return openai.OpenAI(**kwargs)


def _input_to_messages(input_list: list, instructions: str | None) -> list:
    """
    Translate Responses API-style input_list → Chat Completions messages.

    Handled item shapes:
      {"role": "user"|"assistant", "content": ...}          plain message
      {"type": "function_call", "name", "arguments", "call_id"}   tool call (from output)
      {"type": "function_call_output", "call_id", "output"}       tool result (from brain)
      {"type": "message", "role", "content"}                 serialised message
    """
    messages = []
    if instructions:
        messages.append({"role": "system", "content": instructions})

    i = 0
    while i < len(input_list):
        item = input_list[i]

        if not isinstance(item, dict):
            # Raw SDK object — skip; shouldn't appear in Chat Completions mode.
            i += 1
            continue

        t = item.get("type")
        role = item.get("role")

        if role in ("user", "assistant") and t is None:
            messages.append({"role": role, "content": item.get("content", "")})
            i += 1

        elif t == "function_call":
            # Batch consecutive function_call items into one assistant message.
            tool_calls = []
            while i < len(input_list):
                it = input_list[i]
                if isinstance(it, dict) and it.get("type") == "function_call":
                    tool_calls.append({
                        "id": it["call_id"],
                        "type": "function",
                        "function": {
                            "name": it["name"],
                            "arguments": it.get("arguments", "{}"),
                        },
                    })
                    i += 1
                else:
                    break
            messages.append({"role": "assistant", "content": None, "tool_calls": tool_calls})

        elif t == "function_call_output":
            messages.append({
                "role": "tool",
                "tool_call_id": item["call_id"],
                "content": str(item.get("output", "")),
            })
            i += 1

        elif t == "message":
            content = item.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    c.get("text", "") for c in content if isinstance(c, dict)
                )
            messages.append({"role": item.get("role", "assistant"), "content": content})
            i += 1

        else:
            i += 1

    return messages


def chat(input_list: list, tools: bool = True, instructions: str = None, max_tokens: int = 2000) -> dict:
    """
    Make one LLM call. Returns:
    {
        "text": str or None,
        "tool_calls": [{"name": str, "arguments": dict, "call_id": str}],
        "output": list,   # items to append back to input_list in brain.py
    }
    """
    client = _client()
    if _USE_CHAT_COMPLETIONS:
        return _chat_completions(client, input_list, tools, instructions, max_tokens)
    return _responses_api(client, input_list, tools, instructions, max_tokens)


def _responses_api(client, input_list, tools, instructions, max_tokens) -> dict:
    """Original OpenAI Responses API path."""
    kwargs: dict = {
        "model": config["model"],
        "input": input_list,
        "max_output_tokens": max_tokens,
    }
    if instructions:
        kwargs["instructions"] = instructions
    if tools:
        kwargs["tools"] = TOOLS

    response = client.responses.create(**kwargs)

    text_parts = []
    tool_calls = []
    for item in response.output:
        if item.type == "message":
            for content in item.content:
                if hasattr(content, "text"):
                    text_parts.append(content.text)
        elif item.type == "function_call":
            tool_calls.append({
                "name": item.name,
                "arguments": json.loads(item.arguments),
                "call_id": item.call_id,
            })

    return {
        "text": "\n".join(text_parts) if text_parts else None,
        "tool_calls": tool_calls,
        "output": response.output,
    }


def _chat_completions(client, input_list, tools, instructions, max_tokens) -> dict:
    """Chat Completions path — works with z.ai, any OpenAI-compatible provider."""
    messages = _input_to_messages(input_list, instructions)

    kwargs: dict = {
        "model": config["model"],
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if tools:
        kwargs["tools"] = _cc_tools()
        kwargs["tool_choice"] = "auto"

    response = client.chat.completions.create(**kwargs)
    msg = response.choices[0].message

    text = msg.content or None
    tool_calls = []
    output: list = []

    if msg.tool_calls:
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                args = {}
            tool_calls.append({
                "name": tc.function.name,
                "arguments": args,
                "call_id": tc.id,
            })
            # Serialise as dict so brain.py can append it to input_list.
            output.append({
                "type": "function_call",
                "name": tc.function.name,
                "arguments": tc.function.arguments,
                "call_id": tc.id,
            })
    else:
        if text:
            output = [{"type": "message", "role": "assistant", "content": text}]

    return {
        "text": text,
        "tool_calls": tool_calls,
        "output": output,
    }


def embed(text: str) -> list[float]:
    """Get an embedding vector for a text string."""
    client = _client()
    model = config.get("embedding_model", "text-embedding-3-small")
    response = client.embeddings.create(model=model, input=text)
    return response.data[0].embedding


def chat_short(input_list: list, instructions: str = None) -> str:
    """Short LLM call (no tools) — just returns text."""
    result = chat(input_list, tools=False, instructions=instructions)
    return result["text"] or ""
