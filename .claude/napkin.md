# Napkin

## Corrections
| Date | Source | What Went Wrong | What To Do Instead |
|------|--------|----------------|-------------------|

## User Preferences
- (accumulate here as you learn them)

## Patterns That Work
- (approaches that succeeded)

## Patterns That Don't Work
- (approaches that failed and why)

## Domain Notes
- (project/domain context that matters)
| 2026-02-17 | self | Assumed frontend output message `content` was always an array; Chat Completions path provides a string and can break rendering/order. | Normalize backend output shape and make frontend renderer accept both string and array content formats. |

## Domain Notes
- z.ai / OpenAI-compatible Chat Completions can return assistant message content as a plain string; the existing UI expected Responses-style content arrays.
| 2026-02-17 | self | Patching introduced mixed CRLF/LF in edited files, making diffs noisy. | Normalize edited files to LF before final validation (`\r\n` -> `\n`). |
| 2026-02-17 | self | Initially fixed only chat payload parsing; separate traceback indicated unsandboxed Python invocation could bypass rewrite if command used path-like python executable names. | Detect python executables by first token basename (`python`, `python3`, `python3.x`) and always route through `pysandbox.py`. |
| 2026-02-17 | self | Chat feed treated internal nudges and tool outputs as user-side messages, making both sides look LLM/tool-driven. | In `_serialize_input`, emit only real owner/user text as `role: user`; mark internal prompts/tool plumbing with non-user types so UI ignores them. |
| 2026-02-17 | self | Single-slot `_user_message` could overwrite earlier sends before the next think cycle. | Queue user messages with FIFO (`_user_messages`) so no sends are silently lost. |
| 2026-02-17 | self | User messages were popped before inbox handling, so an inbox alert could overwrite and effectively drop the queued user message. | In `_build_input`, gate inbox override with `and not has_user_msg` so direct user messages always win that cycle. |
| 2026-02-17 | self | Frontend renderer treated every `function_call_output` as left-side chat. | Render `function_call_output` only when it matches a quoted user reply (`They say: "..."`); ignore generic tool outputs. |
