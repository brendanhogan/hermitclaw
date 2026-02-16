# Multi-LLM Provider Support

## Goal

Allow HermitClaw to use LLM providers beyond OpenAI — specifically OpenAI-compatible providers like OpenRouter, local models, and others that speak the Chat Completions API format.

## Decisions

- **OpenAI keeps Responses API**; other providers use Chat Completions API
- **Adapter pattern in providers.py** — same public interface, internal routing
- **brain.py unchanged** — translation happens entirely inside providers.py
- **Embeddings**: use provider's embeddings if available, fall back to OpenAI
- **Config**: flat `provider` field in config.yaml, not per-provider sections

## Configuration

```yaml
provider: "openai"              # "openai" | "openrouter" | "custom"
model: "gpt-4.1"
api_key: null                   # or via OPENAI_API_KEY / OPENROUTER_API_KEY
base_url: null                  # required for "custom" provider
embedding_model: "text-embedding-3-small"
```

Provider presets:
- `"openai"` — Responses API, api.openai.com, supports web_search_preview
- `"openrouter"` — Chat Completions API, openrouter.ai/api/v1, env var OPENROUTER_API_KEY
- `"custom"` — Chat Completions API, requires base_url

Env var resolution in config.py:
- `HERMITCLAW_PROVIDER` overrides provider
- `HERMITCLAW_BASE_URL` overrides base_url
- API key: tries provider-specific env var first, falls back to OPENAI_API_KEY

## Architecture

### Public interface (unchanged)

```python
chat(input_list, tools=True, instructions=None, max_tokens=300) -> dict
chat_short(input_list, instructions=None) -> str
embed(text) -> list[float]
```

### Internal routing

`chat()` checks `config["provider"]`:

**OpenAI path** (existing code):
- `responses.create()` with Responses API format
- Supports web_search_preview tool type
- Returns {"text", "tool_calls", "output"} as today

**Chat Completions path** (new):
- `chat.completions.create()` with standard messages format
- Translation layer converts between formats
- Returns same {"text", "tool_calls", "output"} dict

### Translation layer

1. **Tools**: Filter out web_search_preview, convert function tools to Chat Completions format
2. **Input -> Messages**: role-based messages pass through, function_call_output becomes {"role": "tool", ...}
3. **Instructions**: Prepended as {"role": "system", ...} message (Chat Completions has no top-level instructions param)
4. **Params**: max_output_tokens -> max_tokens
5. **Response -> Output**: Normalize Chat Completions response into same return dict, including synthetic output list for brain.py's tool loop

### Embeddings

- embed() creates client with right base_url/key for the configured provider
- Falls back to OpenAI if provider doesn't support embeddings (requires OPENAI_API_KEY)

## Edge Cases

- **Missing API key**: Validate at startup in load_config(), fail fast with clear error
- **web_search_preview**: Silently dropped for non-OpenAI providers (crab has fewer tools)
- **Model names**: User responsibility to set correct format per provider (e.g. openai/gpt-4.1 for OpenRouter)
- **Tool call IDs**: Normalized between call_id (Responses) and tool_call_id (Completions)

## Files Changed

- `config.yaml` — add provider, base_url fields
- `hermitclaw/config.py` — provider-aware env var resolution, validation
- `hermitclaw/providers.py` — add Chat Completions path, translation functions
- `brain.py` — no changes
