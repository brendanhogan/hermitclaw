"""LLM provider routing — Responses API (OpenAI) or Chat Completions (everything else)."""

import json
import logging
import os

import httpx
import openai
from hermitclaw.config import config

logger = logging.getLogger("hermitclaw.providers")

# Max chars of tool result content sent to the model.
# Longer results are truncated to avoid hitting cloud API limits.
MAX_TOOL_CONTENT = 16000


def _log_error_response(response: httpx.Response) -> None:
    """Event hook: log 4xx/5xx response body immediately (before retries consume it)."""
    if response.status_code >= 400:
        try:
            response.read()
            body = response.text[:1000] if response.text else "(empty)"
        except Exception:
            body = "(could not read)"
        logger.error(
            "Ollama/API HTTP %s: %s | url=%s",
            response.status_code,
            body,
            str(response.url),
        )


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
                "message": {
                    "type": "string",
                    "description": "What you say back to them",
                }
            },
            "required": ["message"],
        },
    },
    {
        "type": "function",
        "name": "fetch_url",
        "description": (
            "Fetch the content of a web page. Use this for research when you need to read "
            "an article, documentation, or any URL. Returns the page content (HTML or text). "
            "Only http and https URLs are allowed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch (must start with http:// or https://)",
                }
            },
            "required": ["url"],
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
                    "enum": [
                        "desk",
                        "bookshelf",
                        "window",
                        "plant",
                        "bed",
                        "rug",
                        "center",
                    ],
                }
            },
            "required": ["location"],
        },
    },
]

# Ollama cloud web search tools — for minimax-m2.5:cloud etc. when OLLAMA_API_KEY is set
OLLAMA_WEB_TOOLS = [
    {
        "type": "function",
        "name": "web_search",
        "description": (
            "Search the web for current information. Use for research, fact-checking, "
            "or finding recent news. Returns titles, URLs, and content snippets."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {
                    "type": "integer",
                    "description": "Max results to return (default 5, max 10)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "web_fetch",
        "description": (
            "Fetch the full content of a specific URL. Use after web_search to read "
            "a page in detail. Returns page title, content, and links."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to fetch (e.g. https://...)",
                }
            },
            "required": ["url"],
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
        result.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("parameters", {}),
                },
            }
        )
    return result


def _translate_input_to_messages(
    input_list: list, instructions: str | None
) -> list[dict]:
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
            content = str(item.get("output", ""))
            if len(content) > MAX_TOOL_CONTENT:
                content = content[:MAX_TOOL_CONTENT] + "\n...(truncated)"
            tool_msg = {
                "role": "tool",
                "content": content,
            }
            # v1/chat/completions is OpenAI-compatible; use tool_call_id
            call_id = item.get("call_id")
            if call_id:
                tool_msg["tool_call_id"] = call_id
            if config["provider"] == "custom":
                # Also send tool_name for Ollama cloud models that may expect it
                tool_msg["tool_name"] = item.get("name", "")
            messages.append(tool_msg)
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
            result.append(
                {"type": "image_url", "image_url": {"url": part["image_url"]}}
            )
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
        for i, tc in enumerate(message.tool_calls):
            # Ollama may omit id; ensure we have one for tool_call_id in follow-up
            call_id = tc.id or f"call_{tc.function.name}_{i}"
            tool_calls.append(
                {
                    "name": tc.function.name,
                    "arguments": json.loads(tc.function.arguments),
                    "call_id": call_id,
                }
            )

        # Build a synthetic assistant message for brain.py's input_list
        output.append(
            {
                "role": "assistant",
                "content": text,
                "tool_calls": [
                    {
                        "id": tc.id or f"call_{tc.function.name}_{i}",
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for i, tc in enumerate(message.tool_calls)
                ],
            }
        )

    return {"text": text, "tool_calls": tool_calls, "output": output}


def _client() -> openai.OpenAI:
    return openai.OpenAI(api_key=config["api_key"])


def _uses_responses_api() -> bool:
    """Returns True if the configured provider uses the OpenAI Responses API."""
    return config["provider"] == "openai"


def _completions_client() -> openai.OpenAI:
    """Create an OpenAI client configured for Chat Completions (with base_url)."""
    api_key = config["api_key"]
    if not api_key and config.get("base_url"):
        # Ollama and similar local providers don't need a real key;
        # the SDK requires something, so use a placeholder
        api_key = "ollama"
    kwargs = {"api_key": api_key}
    if config.get("base_url"):
        kwargs["base_url"] = config["base_url"]
        # Local/cloud models (Ollama, etc.) can return transient 500s — retry more
        kwargs["max_retries"] = 5
        # Log 500 response bodies immediately (before retries)
        http_client = httpx.Client(
            event_hooks={"response": [_log_error_response]},
        )
        kwargs["http_client"] = http_client
    return openai.OpenAI(**kwargs)


def _chat_responses(
    input_list: list,
    tools: bool = True,
    instructions: str = None,
    max_tokens: int = 300,
) -> dict:
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
            tool_calls.append(
                {
                    "name": item.name,
                    "arguments": json.loads(item.arguments),
                    "call_id": item.call_id,
                }
            )

    return {
        "text": "\n".join(text_parts) if text_parts else None,
        "tool_calls": tool_calls,
        "output": response.output,
    }


def _summarize_messages_for_log(messages: list) -> list:
    """Return a safe summary of messages for logging (truncate long content)."""
    out = []
    for m in messages:
        role = m.get("role", "?")
        content = m.get("content", "")
        if isinstance(content, str) and len(content) > 80:
            content = content[:80] + "..."
        elif isinstance(content, list):
            content = f"[{len(content)} parts]"
        d = {"role": role, "content": str(content)[:100]}
        if m.get("role") == "tool":
            d["tool_name"] = m.get("tool_name", "?")
            d["tool_call_id"] = (
                m.get("tool_call_id", "?")[:20] if m.get("tool_call_id") else None
            )
        if m.get("role") == "assistant" and m.get("tool_calls"):
            d["tool_calls"] = [
                {"name": tc.get("function", {}).get("name")} for tc in m["tool_calls"]
            ]
        out.append(d)
    return out


def _chat_completions(
    input_list: list,
    tools: bool = True,
    instructions: str = None,
    max_tokens: int = 300,
) -> dict:
    """Make a Chat Completions API call. Same return format as _chat_responses."""
    messages = _translate_input_to_messages(input_list, instructions)

    kwargs = {
        "model": config["model"],
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if tools:
        completions_tools = _translate_tools_for_completions(TOOLS)
        if config.get("ollama_api_key") and config["provider"] == "custom":
            ollama_tools = _translate_tools_for_completions(OLLAMA_WEB_TOOLS)
            completions_tools = completions_tools + ollama_tools
        if completions_tools:
            kwargs["tools"] = completions_tools

    summary = _summarize_messages_for_log(messages)
    logger.info(
        "chat_completions request: model=%s provider=%s msg_count=%d summary=%s",
        config["model"],
        config["provider"],
        len(messages),
        json.dumps(summary, default=str),
    )
    try:
        response = _completions_client().chat.completions.create(**kwargs)
        return _normalize_completions_response(response)
    except Exception as e:
        body = ""
        for attr in ("response", "http_response", "body"):
            val = getattr(e, attr, None)
            if val is not None:
                if hasattr(val, "text"):
                    body = (val.text or "")[:500]
                    break
                if isinstance(val, str):
                    body = val[:500]
                    break
        logger.exception(
            "chat_completions failed: %s | response_body=%s",
            e,
            body or "(none)",
        )
        raise


def chat(
    input_list: list,
    tools: bool = True,
    instructions: str = None,
    max_tokens: int = 300,
) -> dict:
    """Make an LLM call. Routes to Responses API or Chat Completions based on provider config.

    Returns:
    {
        "text": str or None,
        "tool_calls": [{"name": str, "arguments": dict, "call_id": str}],
        "output": list,   # raw output (for appending back to input on follow-up calls)
    }
    """
    if _uses_responses_api():
        return _chat_responses(input_list, tools, instructions, max_tokens)
    return _chat_completions(input_list, tools, instructions, max_tokens)


def embed(text: str) -> list[float]:
    """Get an embedding vector for a text string.

    Uses the configured provider's embeddings endpoint. Falls back to OpenAI
    if the provider doesn't support embeddings (requires OPENAI_API_KEY).
    """
    from hermitclaw.config import config as cfg

    model = cfg.get("embedding_model", "text-embedding-3-small")

    # Try configured provider first
    try:
        client = _completions_client() if not _uses_responses_api() else _client()
        response = client.embeddings.create(model=model, input=text)
        return response.data[0].embedding
    except Exception:
        if _uses_responses_api():
            raise  # OpenAI is already the provider, don't retry
        # Fall back to OpenAI for embeddings
        fallback_key = os.environ.get("OPENAI_API_KEY")
        if not fallback_key:
            raise
        fallback = openai.OpenAI(api_key=fallback_key)
        response = fallback.embeddings.create(model=model, input=text)
        return response.data[0].embedding


def chat_short(input_list: list, instructions: str = None) -> str:
    """Short LLM call (for importance scoring, reflections) — just returns text, no tools."""
    result = chat(input_list, tools=False, instructions=instructions)
    return result["text"] or ""
