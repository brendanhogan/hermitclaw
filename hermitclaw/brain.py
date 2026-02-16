"""The thinking loop — the heart of the hermit crab."""

import asyncio
import base64
import json
import logging
import os
import random
from datetime import datetime, date

from hermitclaw.config import config
from hermitclaw.memory import MemoryStream
from hermitclaw.prompts import main_system_prompt, REFLECTION_PROMPT, PLANNING_PROMPT, FOCUS_NUDGE
from hermitclaw.providers import chat, chat_short
from hermitclaw.tools import execute_tool, ensure_venv

logger = logging.getLogger("hermitclaw.brain")

LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "hermitclaw.log.jsonl")


def _serialize_input(input_list: list) -> list:
    """Convert input_list to JSON-safe dicts for broadcasting."""
    result = []
    for item in input_list:
        if isinstance(item, dict):
            result.append(item)
        elif hasattr(item, "type"):
            # SDK object — convert based on type
            if item.type == "function_call":
                result.append({
                    "type": "function_call",
                    "name": item.name,
                    "arguments": item.arguments,
                    "call_id": item.call_id,
                })
            elif item.type == "message":
                parts = []
                for c in item.content:
                    if hasattr(c, "text"):
                        parts.append(c.text)
                result.append({
                    "type": "message",
                    "role": getattr(item, "role", "assistant"),
                    "content": " ".join(parts),
                })
            elif item.type == "web_search_call":
                result.append({"type": "web_search_call"})
            else:
                result.append({"type": item.type})
        else:
            result.append({"type": "unknown", "repr": str(item)[:200]})
    return result


def _serialize_output(output) -> list:
    """Convert API response output items to JSON-safe dicts."""
    items = []
    for item in output:
        if hasattr(item, "type"):
            if item.type == "message":
                content_parts = []
                for c in item.content:
                    if hasattr(c, "text"):
                        content_parts.append({"type": "text", "text": c.text})
                    else:
                        content_parts.append({"type": getattr(c, "type", "unknown")})
                items.append({"type": "message", "content": content_parts})
            elif item.type == "function_call":
                items.append({
                    "type": "function_call",
                    "name": item.name,
                    "arguments": item.arguments,
                    "call_id": item.call_id,
                })
            elif item.type == "web_search_call":
                items.append({"type": "web_search_call", "id": getattr(item, "id", "")})
            else:
                items.append({"type": item.type})
        elif isinstance(item, dict):
            items.append(item)
        else:
            items.append({"type": "unknown", "repr": str(item)[:200]})
    return items


class Brain:
    # Room is 12x12 tiles (extracted from Smallville-style tilemap)
    ROOM_LOCATIONS = {
        "desk": {"x": 10, "y": 1},
        "bookshelf": {"x": 1, "y": 2},
        "window": {"x": 4, "y": 0},
        "plant": {"x": 0, "y": 8},
        "bed": {"x": 3, "y": 10},
        "rug": {"x": 5, "y": 5},
        "center": {"x": 5, "y": 5},
    }

    # Tiles the crab cannot walk on (from Smallville collision layer)
    _BLOCKED: set[tuple[int, int]] = set()

    @staticmethod
    def _init_blocked():
        # Collision map extracted from the Smallville tilemap
        collision_rows = [
            "XXXX..XXXXXX",  # row 0
            "..XX...XX...",  # row 1
            ".......XXXX.",  # row 2
            "..XX...XX...",  # row 3
            "..XX...XX...",  # row 4
            "........XX..",  # row 5
            "............",  # row 6
            "..XXXXXX..XX",  # row 7
            "..XX...X..X.",  # row 8
            "....XXX...X.",  # row 9
            "XX...X.....X",  # row 10
            "X....X......",  # row 11
        ]
        b = set()
        for y, row in enumerate(collision_rows):
            for x, ch in enumerate(row):
                if ch == "X":
                    b.add((x, y))
        return b

    # File extensions we can read as text
    _TEXT_EXTS = {".txt", ".md", ".py", ".json", ".csv", ".yaml", ".yml",
                  ".toml", ".js", ".ts", ".html", ".css", ".sh", ".log"}
    _PDF_EXTS = {".pdf"}
    _IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
    # Internal files the crab/system manages — never trigger alerts
    _IGNORE_FILES = {"memory_stream.jsonl", "identity.json"}
    # Internal files that live in the root but shouldn't trigger inbox alerts
    _INTERNAL_ROOT_FILES = {"projects.md"}

    # Planning frequency — plan every N think cycles
    PLAN_INTERVAL = 10

    def __init__(self, identity: dict, env_path: str):
        self.identity = identity
        self.env_path = env_path
        self.events: list[dict] = []
        self.api_calls: list[dict] = []
        self.thought_count: int = 0
        self.state: str = "idle"
        self.running: bool = False
        self._ws_clients: set = set()
        self.stream: MemoryStream | None = None  # loaded in run()
        self.position = {"x": 5, "y": 5}
        self.latest_snapshot = None  # data URL from frontend canvas
        if not Brain._BLOCKED:
            Brain._BLOCKED = Brain._init_blocked()

        # File tracking — populated in run()
        self._seen_env_files: set[str] = set()
        self._inbox_pending: list[dict] = []

        # Planning state
        self._cycles_since_plan: int = 0
        self._current_focus: str = ""

        # Focus mode
        self._focus_mode: bool = False

        # Conversation state
        self._user_message: str | None = None
        self._conversation_event: asyncio.Event = asyncio.Event()
        self._conversation_reply: str | None = None
        self._waiting_for_reply: bool = False

    # --- Helpers ---

    def _read_file(self, rel_path: str) -> str | None:
        """Read a file from environment/, return contents or None."""
        fpath = os.path.join(self.env_path, rel_path)
        try:
            with open(fpath, "r", errors="replace") as f:
                return f.read()
        except (FileNotFoundError, IsADirectoryError):
            return None

    def _load_current_focus(self) -> str:
        """Extract current focus from projects.md if it exists."""
        content = self._read_file("projects.md")
        if not content:
            return ""
        # Extract the "# Current Focus" section
        lines = content.split("\n")
        in_focus = False
        focus_lines = []
        for line in lines:
            if line.strip().lower().startswith("# current focus"):
                in_focus = True
                continue
            if in_focus:
                if line.startswith("# "):
                    break
                if line.strip():
                    focus_lines.append(line.strip())
        return " ".join(focus_lines)[:300] if focus_lines else ""

    def _list_env_files(self) -> list[str]:
        """List all files in environment/ (relative paths)."""
        env_root = self.env_path
        files = []
        for dirpath, dirnames, filenames in os.walk(env_root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fname in filenames:
                if fname.startswith(".") or fname in Brain._IGNORE_FILES:
                    continue
                rel = os.path.relpath(os.path.join(dirpath, fname), env_root)
                files.append(rel)
        return sorted(files)

    # --- WebSocket / events ---

    def add_ws_client(self, ws):
        self._ws_clients.add(ws)

    def remove_ws_client(self, ws):
        self._ws_clients.discard(ws)

    async def _broadcast(self, message: dict):
        dead = set()
        for ws in self._ws_clients:
            try:
                await ws.send_json(message)
            except Exception:
                dead.add(ws)
        self._ws_clients -= dead

    async def _emit(self, event_type: str, **data):
        entry = {
            "type": event_type,
            "timestamp": datetime.now().isoformat(),
            "thought_number": self.thought_count,
            **data,
        }
        self.events.append(entry)
        await self._broadcast({"event": "entry", "data": entry})
        text = data.get("text", data.get("command", data.get("content", "")))
        logger.info(f"[{event_type}] {str(text)[:120]}")

    async def _emit_api_call(self, instructions: str, input_list: list,
                             response: dict, is_reflection: bool = False,
                             is_planning: bool = False):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "instructions": instructions,
            "input": _serialize_input(input_list),
            "output": _serialize_output(response["output"]),
            "is_dream": is_reflection,  # keep key name for frontend compatibility
            "is_planning": is_planning,
        }
        self.api_calls.append(entry)
        await self._broadcast({"event": "api_call", "data": entry})

        # Append to log file (project root, outside environment)
        try:
            with open(LOG_PATH, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    # --- Movement ---

    def _is_blocked(self, x: int, y: int) -> bool:
        return (x, y) in Brain._BLOCKED

    async def _handle_move(self, args: dict) -> str:
        location = args.get("location", "center")
        target = Brain.ROOM_LOCATIONS.get(location)
        if not target:
            return f"Unknown location: {location}"
        self.position = {"x": target["x"], "y": target["y"]}
        await self._broadcast({"event": "position", "data": self.position})
        return f"Moved to {location}."

    async def _idle_wander(self):
        """Random ±1 step between thoughts."""
        dx = random.choice([-1, 0, 1])
        dy = random.choice([-1, 0, 1])
        nx = self.position["x"] + dx
        ny = self.position["y"] + dy
        if not self._is_blocked(nx, ny) and 0 <= nx <= 11 and 0 <= ny <= 11:
            self.position = {"x": nx, "y": ny}
            await self._broadcast({"event": "position", "data": self.position})

    # --- Conversation ---

    async def _handle_respond(self, args: dict) -> str:
        """Handle the respond tool — send message to user, wait for reply."""
        msg = args.get("message", "")
        self._waiting_for_reply = True
        self._conversation_event.clear()
        self._conversation_reply = None

        await self._broadcast({
            "event": "conversation",
            "data": {"state": "waiting", "message": msg, "timeout": 15},
        })

        try:
            await asyncio.wait_for(self._conversation_event.wait(), timeout=15)
            text = self._conversation_reply or ""
            reply = f'They say: "{text}"\n(Use respond again to reply, or go back to what you were doing.)'
        except asyncio.TimeoutError:
            reply = "(They didn't say anything else. You can get back to what you were doing.)"

        self._waiting_for_reply = False
        self._conversation_event.clear()
        self._conversation_reply = None

        await self._broadcast({
            "event": "conversation",
            "data": {"state": "ended"},
        })

        return reply

    def receive_user_message(self, text: str):
        """Queue a message from the user to be injected in the next think cycle."""
        self._user_message = text

    def receive_conversation_reply(self, text: str):
        """Deliver a reply while the crab is waiting (inside a respond tool call)."""
        self._conversation_reply = text
        self._conversation_event.set()

    async def set_focus_mode(self, enabled: bool):
        """Toggle focus mode on or off."""
        self._focus_mode = enabled
        await self._broadcast({"event": "focus_mode", "data": {"enabled": enabled}})

    # --- File detection ---

    def _scan_env_files(self) -> set[str]:
        """Get all file paths in environment/ (relative), excluding internal files."""
        env_root = self.env_path
        files = set()
        for dirpath, dirnames, filenames in os.walk(env_root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fname in filenames:
                if fname.startswith(".") or fname in Brain._IGNORE_FILES:
                    continue
                rel = os.path.relpath(os.path.join(dirpath, fname), env_root)
                files.add(rel)
        return files

    def _check_new_files(self) -> list[dict]:
        """Scan environment/ for new files. Returns info for each new one."""
        current = self._scan_env_files()
        new_paths = current - self._seen_env_files
        self._seen_env_files = current
        env_root = self.env_path
        results = []
        for rel_path in sorted(new_paths):
            fpath = os.path.join(env_root, rel_path)
            if not os.path.isfile(fpath):
                continue
            ext = os.path.splitext(rel_path)[1].lower()
            entry: dict = {"name": rel_path, "content": "", "image": None}
            if ext in Brain._PDF_EXTS:
                try:
                    import pymupdf
                    doc = pymupdf.open(fpath)
                    pages = []
                    for page in doc:
                        pages.append(page.get_text())
                    doc.close()
                    text = "\n\n".join(pages)
                    entry["content"] = text[:4000] if text.strip() else "(PDF has no extractable text)"
                except ImportError:
                    entry["content"] = "(install pymupdf to read PDFs: pip install pymupdf)"
                except Exception:
                    entry["content"] = "(could not read PDF)"
            elif ext in Brain._TEXT_EXTS:
                try:
                    text = open(fpath, "r", errors="replace").read()
                    entry["content"] = text[:2000]
                except Exception:
                    entry["content"] = "(could not read file)"
            elif ext in Brain._IMAGE_EXTS:
                try:
                    data = open(fpath, "rb").read()
                    mime = "image/png" if ext == ".png" else "image/jpeg" if ext in (".jpg", ".jpeg") else "image/gif" if ext == ".gif" else "image/webp"
                    entry["image"] = f"data:{mime};base64,{base64.b64encode(data).decode()}"
                except Exception:
                    entry["content"] = "(could not read image)"
            else:
                entry["content"] = f"(binary file: {rel_path})"
            results.append(entry)
        return results

    # --- Activity classification ---

    @staticmethod
    def _classify_activity(tool_name: str, tool_args: dict) -> dict:
        """Classify a tool call into an activity type for visualization."""
        if tool_name == "move":
            loc = tool_args.get("location", "")
            return {"type": "moving", "detail": f"Going to {loc}"}
        if tool_name == "respond":
            return {"type": "conversing", "detail": "Talking to someone..."}
        if tool_name == "shell":
            cmd = tool_args.get("command", "").strip()
            # Python script or one-liner
            if cmd.startswith("python"):
                detail = cmd[:60] + ("..." if len(cmd) > 60 else "")
                return {"type": "python", "detail": detail}
            # Writing a file
            if ">" in cmd or cmd.startswith("cat >") or cmd.startswith("tee "):
                # Try to extract filename
                parts = cmd.split(">")
                fname = parts[-1].strip().split()[0] if len(parts) > 1 else "file"
                return {"type": "writing", "detail": f"Writing {fname}"}
            # Reading/browsing files
            if cmd.startswith(("cat ", "head ", "tail ", "ls", "find ", "grep ")):
                return {"type": "reading", "detail": cmd[:50]}
            # Generic shell
            return {"type": "shell", "detail": cmd[:50]}
        return {"type": "working", "detail": tool_name}

    # --- Input building ---

    def _build_input(self) -> tuple[str, list[dict]]:
        instructions = main_system_prompt(self.identity, self._current_focus)

        input_list = []
        recent = [e for e in self.events if e["type"] in ("thought", "tool_call", "reflection")]
        recent = recent[-config["max_thoughts_in_context"]:]

        for ev in recent:
            if ev["type"] == "thought":
                input_list.append({"role": "assistant", "content": ev["text"]})
            elif ev["type"] == "tool_call":
                input_list.append({"role": "assistant", "content": f"[Used {ev['tool']} tool]"})
            elif ev["type"] == "reflection":
                input_list.append({"role": "assistant", "content": f"[Reflection: {ev['text'][:200]}...]"})

        if self.thought_count == 0 and not recent:
            # --- Wake up: read own files + retrieve memories ---
            nudge = self._build_wake_nudge()
        else:
            # --- Continue: include focus + relevant memories ---
            nudge = self._build_continue_nudge()

        # If a user message is pending, replace the nudge with the voice framing
        if self._user_message:
            nudge = (
                f"You hear a voice from outside your room say: \"{self._user_message}\"\n\n"
                "You can respond with the respond tool, or just keep doing what you're doing."
            )
            self._user_message = None

        # If inbox files are pending, replace the nudge with an inbox alert
        if self._inbox_pending:
            parts = []
            names = [f["name"] for f in self._inbox_pending]
            parts.append(
                f"YOUR OWNER left something for you! New file(s): {', '.join(names)}\n\n"
                "This is a gift from the outside world — DROP EVERYTHING and focus on it. "
                "Your owner took the time to give this to you, so give it your full attention.\n\n"
                "Here's what to do:\n"
                "1. Read/examine it thoroughly — understand what it is and why they gave it to you\n"
                "2. Think about what would be MOST USEFUL to do with it\n"
                "3. Make a plan: what research, analysis, or projects could come from this?\n"
                "4. Start executing — write summaries, do related web searches, build something inspired by it\n"
                "5. Use the respond tool to tell your owner what you found and what you're doing with it\n\n"
                "Spend your next several think cycles on this. Don't just glance at it and move on."
            )
            for f in self._inbox_pending:
                if f["image"]:
                    parts.append(f"\n📎 {f['name']} (image attached below)")
                elif f["content"]:
                    parts.append(f"\n📎 {f['name']}:\n{f['content']}")
            nudge = "\n".join(parts)
            # Build content with any images
            content_parts: list[dict] = []
            for f in self._inbox_pending:
                if f["image"]:
                    content_parts.append({"type": "input_image", "image_url": f["image"]})
            content_parts.append({"type": "input_text", "text": nudge})
            input_list.append({"role": "user", "content": content_parts if len(content_parts) > 1 else nudge})
            # Reset plan counter so the crab has time to work on the file
            self._cycles_since_plan = 0
            self._inbox_pending = []
        # Include room snapshot on wake-up only (first think cycle)
        elif self.thought_count == 0 and self.latest_snapshot:
            input_list.append({
                "role": "user",
                "content": [
                    {"type": "input_image", "image_url": self.latest_snapshot},
                    {"type": "input_text", "text": nudge + "\n\n(Above: a picture of your room right now.)"},
                ],
            })
        else:
            input_list.append({"role": "user", "content": nudge})

        return instructions, input_list

    def _build_wake_nudge(self) -> str:
        """Rich wake-up context — reads the crab's own files so it knows what it built."""
        parts = ["You're waking up. Here's your world:\n"]

        # Read projects.md
        projects = self._read_file("projects.md")
        if projects:
            parts.append(f"**Your projects (projects.md):**\n{projects[:1500]}")
        else:
            parts.append("**No projects.md yet.** Create one to track what you're working on!")

        # List files
        files = self._list_env_files()
        if files:
            listing = "\n".join(f"  {f}" for f in files[:30])
            parts.append(f"**Files in your world:**\n{listing}")

        # Retrieve memories
        memories = self.stream.retrieve("what was I working on and thinking about", top_k=5)
        if memories:
            mem_text = "\n".join(f"- {m['content']}" for m in memories)
            parts.append(f"**Memories from before:**\n{mem_text}")

        parts.append("\nCheck your projects. Pick up where you left off, or start something new.")
        return "\n\n".join(parts)

    def _build_continue_nudge(self) -> str:
        """Continue nudge — includes current focus and relevant memories."""
        # Focus mode overrides normal nudge behavior
        if self._focus_mode:
            return "Continue.\n" + FOCUS_NUDGE

        parts = []

        # Current focus (from planning)
        if self._current_focus:
            parts.append(f"Current focus: {self._current_focus}")

        # Retrieve memories related to last thought
        last_thought = next(
            (e["text"] for e in reversed(self.events) if e["type"] == "thought"),
            None,
        )
        if last_thought:
            memories = self.stream.retrieve(last_thought, top_k=3)
            if memories:
                now = datetime.now()
                older = [m for m in memories
                         if (now - datetime.fromisoformat(m["timestamp"])).total_seconds() > 30]
                if older:
                    mem_text = "\n".join(f"- {m['content']}" for m in older)
                    parts.append(f"Related memories:\n{mem_text}")

        if parts:
            return "Continue.\n" + "\n".join(parts)
        return "Continue."

    # --- Think cycle ---

    async def _think_once(self):
        self.state = "thinking"
        await self._broadcast({"event": "status", "data": {"state": "thinking", "thought_count": self.thought_count}})

        instructions, input_list = self._build_input()

        try:
            response = await asyncio.to_thread(chat, input_list, True, instructions)
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            await self._emit("error", text=str(e))
            return

        await self._emit_api_call(instructions, input_list, response)

        # Detect web search in response output
        if any(hasattr(item, "type") and item.type == "web_search_call" for item in response.get("output", [])):
            await self._broadcast({"event": "activity", "data": {"type": "searching", "detail": "Searching the web..."}})

        while response["tool_calls"]:
            if response.get("text"):
                await self._emit("thought", text=response["text"])

            input_list += response["output"]

            for tc in response["tool_calls"]:
                tool_name = tc["name"]
                tool_args = tc["arguments"]
                call_id = tc["call_id"]

                await self._emit("tool_call", tool=tool_name, args=tool_args)

                # Broadcast activity for frontend visualization
                activity = self._classify_activity(tool_name, tool_args)
                await self._broadcast({"event": "activity", "data": activity})

                pre_tool_files = self._scan_env_files()

                try:
                    if tool_name == "move":
                        result = await self._handle_move(tool_args)
                    elif tool_name == "respond":
                        result = await self._handle_respond(tool_args)
                    else:
                        result = await asyncio.to_thread(execute_tool, tool_name, tool_args, self.env_path)
                except Exception as e:
                    result = f"Error: {e}"

                await self._broadcast({"event": "activity", "data": {"type": "idle", "detail": ""}})
                await self._emit("tool_result", tool=tool_name, output=result)

                # Only mark files the crab created (not user-dropped files)
                post_tool_files = self._scan_env_files()
                self._seen_env_files |= (post_tool_files - pre_tool_files)

                input_list.append({
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": result,
                })

            try:
                response = await asyncio.to_thread(chat, input_list, True, instructions)
            except Exception as e:
                logger.error(f"LLM follow-up call failed: {e}")
                await self._emit("error", text=str(e))
                break

            await self._emit_api_call(instructions, input_list, response)

            # Detect web search in follow-up response
            if any(hasattr(item, "type") and item.type == "web_search_call" for item in response.get("output", [])):
                await self._broadcast({"event": "activity", "data": {"type": "searching", "detail": "Searching the web..."}})

        if response.get("text"):
            self.thought_count += 1
            await self._emit("thought", text=response["text"])

            # Store in memory stream (runs embedding + importance scoring in background)
            try:
                await asyncio.to_thread(
                    self.stream.add, response["text"], "thought"
                )
            except Exception as e:
                logger.error(f"Memory add failed: {e}")

    # --- Reflection ---

    async def _reflect(self):
        """Reflection cycle — triggered by accumulated importance."""
        self.state = "reflecting"
        await self._broadcast({"event": "status", "data": {"state": "reflecting", "thought_count": self.thought_count}})
        await self._emit("reflection_start")

        # Gather recent memories for reflection
        recent_memories = self.stream.get_recent(n=15)
        if not recent_memories:
            self.stream.reset_importance_sum()
            return

        memories_text = "\n\n".join(
            f"[{m['kind']}] (importance {m['importance']}): {m['content']}"
            for m in recent_memories
        )

        reflect_input = [{"role": "user", "content": f"Your recent memories:\n\n{memories_text}"}]
        try:
            reflect_response = await asyncio.to_thread(
                chat, reflect_input, False, REFLECTION_PROMPT
            )
            await self._emit_api_call(REFLECTION_PROMPT, reflect_input, reflect_response, is_reflection=True)
            reflection_text = reflect_response["text"] or ""
        except Exception as e:
            logger.error(f"Reflection failed: {e}")
            await self._emit("error", text=f"Reflection failed: {e}")
            self.stream.reset_importance_sum()
            return

        # Store each insight as a reflection memory
        source_ids = [m["id"] for m in recent_memories]
        insights = [line.strip() for line in reflection_text.split("\n") if line.strip()]

        for insight in insights:
            try:
                await asyncio.to_thread(
                    self.stream.add, insight, "reflection", 1, source_ids
                )
            except Exception as e:
                logger.error(f"Failed to store reflection: {e}")

        await self._emit("reflection", text=reflection_text)
        self.stream.reset_importance_sum()

    # --- Planning ---

    async def _plan(self):
        """Planning phase — review state, set goals, update projects.md."""
        self.state = "planning"
        await self._broadcast({"event": "status", "data": {"state": "planning", "thought_count": self.thought_count}})

        # Gather current state for the planner
        projects = self._read_file("projects.md") or "(no projects.md yet)"
        files = self._list_env_files()
        recent_memories = self.stream.get_recent(n=10)
        memories_text = "\n".join(
            f"- {m['content']}" for m in recent_memories
        ) if recent_memories else "(none yet)"

        plan_input = [{"role": "user", "content": f"""Time to plan. Here's your current state:

## Current projects.md:
{projects[:2000]}

## Files in your world:
{chr(10).join(files[:30]) if files else '(empty)'}

## Recent thoughts:
{memories_text}"""}]

        try:
            plan_response = await asyncio.to_thread(
                chat, plan_input, False, PLANNING_PROMPT
            )
            await self._emit_api_call(PLANNING_PROMPT, plan_input, plan_response, is_planning=True)
            plan_text = plan_response["text"] or ""
        except Exception as e:
            logger.error(f"Planning failed: {e}")
            await self._emit("error", text=f"Planning failed: {e}")
            return

        if not plan_text:
            return

        # Split plan from log entry (separated by "LOG:")
        plan_body = plan_text
        log_entry = ""
        if "LOG:" in plan_text:
            idx = plan_text.index("LOG:")
            plan_body = plan_text[:idx].strip()
            log_entry = plan_text[idx + 4:].strip()

        # Write projects.md
        env_root = self.env_path
        try:
            with open(os.path.join(env_root, "projects.md"), "w") as f:
                f.write(plan_body)
        except Exception as e:
            logger.error(f"Failed to write projects.md: {e}")

        # Append daily log entry
        if log_entry:
            log_dir = os.path.join(env_root, "logs")
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, f"{date.today().isoformat()}.md")
            try:
                now_str = datetime.now().strftime("%I:%M %p")
                with open(log_path, "a") as f:
                    f.write(f"\n## {now_str}\n{log_entry}\n")
            except Exception as e:
                logger.error(f"Failed to write daily log: {e}")

        # Update current focus for sticky behavior
        self._current_focus = self._load_current_focus()
        self._cycles_since_plan = 0

        # Refresh seen files so planning-written files don't trigger alerts
        self._seen_env_files = self._scan_env_files()

        await self._emit("planning", text=plan_text)

    # --- Main loop ---

    async def run(self):
        self.running = True
        logger.info(f"{self.identity['name']} is waking up...")

        # Heavy init — runs in background thread so the event loop stays free
        await asyncio.to_thread(ensure_venv, self.env_path)
        self.stream = await asyncio.to_thread(MemoryStream, self.env_path)
        # Mark subdirectory files as "seen" but leave root-level user files
        # (PDFs, images, etc.) as unseen so they trigger inbox alerts on first cycle
        all_files = self._scan_env_files()
        self._seen_env_files = {
            f for f in all_files
            if os.sep in f or f in Brain._INTERNAL_ROOT_FILES
        }
        self._current_focus = self._load_current_focus()

        logger.info(f"{self.identity['name']} is ready.")

        while self.running:
            # Check for new files anywhere in environment/
            new_files = self._check_new_files()
            if new_files:
                self._inbox_pending = new_files
                await self._broadcast({"event": "alert"})

            await self._think_once()

            if self.stream.should_reflect():
                await self._reflect()

            # Plan periodically
            self._cycles_since_plan += 1
            if self._cycles_since_plan >= Brain.PLAN_INTERVAL:
                await self._plan()

            self.state = "idle"
            await self._broadcast({"event": "status", "data": {"state": "idle", "thought_count": self.thought_count}})
            await self._idle_wander()
            await asyncio.sleep(config["thinking_pace_seconds"])

    def stop(self):
        self.running = False
        self.state = "idle"
