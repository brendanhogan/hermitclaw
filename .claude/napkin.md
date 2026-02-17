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
