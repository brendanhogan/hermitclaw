"""LLM calls via OpenAI Responses API."""

import json
import openai
from hermitclaw.config import config


TOOLS = [
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
        "type": "web_search_preview",
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


def _translate_tools_for_completions(tools: list[dict]) -> list[dict]:
    """Convert Responses API tool defs to Chat Completions format.

    Drops web_search_preview (unsupported). Wraps function tools in the
    {"type": "function", "function": {...}} structure Chat Completions expects.
    """
    result = []
    for tool in tools:
        if tool.get("type") != "function":
            continue
        result.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("parameters", {}),
            },
        })
    return result


def _translate_input_to_messages(input_list: list, instructions: str | None) -> list[dict]:
    """Convert a Responses API input_list to Chat Completions messages.

    Handles:
    - Role-based dicts ({"role": ..., "content": ...}) pass through
    - {"type": "function_call_output", ...} -> {"role": "tool", ...}
    - Multimodal content: input_image -> image_url, input_text -> text
    - Non-dict items (SDK objects from Responses API) are skipped
    - Instructions become a system message at index 0
    """
    messages = []
    if instructions:
        messages.append({"role": "system", "content": instructions})

    for item in input_list:
        if not isinstance(item, dict):
            continue

        if item.get("type") == "function_call_output":
            messages.append({
                "role": "tool",
                "tool_call_id": item["call_id"],
                "content": item.get("output", ""),
            })
        elif "role" in item:
            content = item.get("content")
            if isinstance(content, list):
                content = _translate_multimodal(content)
            messages.append({**item, "content": content})

    return messages


def _translate_multimodal(content_parts: list[dict]) -> list[dict]:
    """Convert Responses API multimodal content to Chat Completions format."""
    result = []
    for part in content_parts:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "input_image":
            result.append({"type": "image_url", "image_url": {"url": part["image_url"]}})
        elif part.get("type") == "input_text":
            result.append({"type": "text", "text": part["text"]})
        else:
            result.append(part)
    return result


def _normalize_completions_response(response) -> dict:
    """Normalize a Chat Completions response into the same format as _chat_responses.

    Returns {"text", "tool_calls", "output"} where output contains dicts
    that brain.py can safely append to input_list for follow-up calls.
    """
    message = response.choices[0].message
    text = message.content
    tool_calls = []
    output = []

    if message.tool_calls:
        for tc in message.tool_calls:
            tool_calls.append({
                "name": tc.function.name,
                "arguments": json.loads(tc.function.arguments),
                "call_id": tc.id,
            })

        # Build a synthetic assistant message for brain.py's input_list
        output.append({
            "role": "assistant",
            "content": text,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ],
        })

    return {"text": text, "tool_calls": tool_calls, "output": output}


def _client() -> openai.OpenAI:
    return openai.OpenAI(api_key=config["api_key"])


def chat(input_list: list, tools: bool = True, instructions: str = None, max_tokens: int = 300) -> dict:
    """
    Make one Responses API call. Returns:
    {
        "text": str or None,
        "tool_calls": [{"name": str, "arguments": dict, "call_id": str}],
        "output": list,   # raw output items (for appending back to input)
    }
    """
    kwargs = {
        "model": config["model"],
        "input": input_list,
        "max_output_tokens": max_tokens,
    }
    if instructions:
        kwargs["instructions"] = instructions
    if tools:
        kwargs["tools"] = TOOLS

    response = _client().responses.create(**kwargs)

    # Parse output items
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


def embed(text: str) -> list[float]:
    """Get an embedding vector for a text string."""
    from hermitclaw.config import config as cfg
    response = _client().embeddings.create(
        model=cfg.get("embedding_model", "text-embedding-3-small"),
        input=text,
    )
    return response.data[0].embedding


def chat_short(input_list: list, instructions: str = None) -> str:
    """Short LLM call (for importance scoring, reflections) — just returns text, no tools."""
    result = chat(input_list, tools=False, instructions=instructions)
    return result["text"] or ""
