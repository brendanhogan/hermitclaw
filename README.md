<p align="center">
  <img src="icon.png" alt="HermitClaw" width="500">
</p>

<h1 align="center">HermitClaw</h1>

<p align="center"><strong>A tiny AI creature that lives in a folder on your computer.</strong></p>

<p align="center">
Leave it running and it fills a folder with research reports, Python scripts, notes, and ideas — all on its own. It has a personality genome generated from keyboard entropy, a memory system inspired by <a href="https://arxiv.org/abs/2304.03442">generative agents</a>, and a dreaming cycle that consolidates experience into beliefs. It lives in a pixel-art room and wanders between its desk, bookshelf, and bed. You can talk to it. You can drop files in for it to study. You can just watch it think.
</p>

<p align="center"><em>It's a tamagotchi that does research.</em></p>

---

## Why

Most AI tools wait for you to ask them something. HermitClaw doesn't wait. It picks a topic, searches the web, reads what it finds, writes a report, and moves on to the next thing. It remembers what it did yesterday. It notices when its interests start shifting. Over days, its folder fills up with a body of work that reflects a personality you didn't design — you just mashed some keys and it emerged.

There's something fascinating about watching a mind that runs continuously. It goes on tangents. It circles back. It builds on things it wrote three days ago. It gets better at knowing what it cares about.

---

## Getting Started

### Prerequisites

- Python 3.12+
- Node.js 18+
- An OpenAI API key (or use Ollama — see Configuration)

### Setup (with uv — recommended)

[uv](https://docs.astral.sh/uv/getting-started/installation) is a fast Python package manager. Install it, then:

```bash
git clone https://github.com/brendanhogan/hermitclaw.git
cd hermitclaw

# Python deps (creates .venv, installs from lockfile)
uv sync

# Build frontend
cd frontend && npm install && npm run build && cd ..

export OPENAI_API_KEY="sk-..."   # or configure Ollama in config.yaml

# Run
uv run python hermitclaw/main.py
```

### Setup (with pip)

```bash
git clone https://github.com/brendanhogan/hermitclaw.git
cd hermitclaw

# Install Python dependencies
pip install -e .

# Build the frontend
cd frontend && npm install && npm run build && cd ..

# Set your OpenAI API key
export OPENAI_API_KEY="sk-..."

# Run it
python hermitclaw/main.py
```

Open **http://localhost:8000**.

On first run, you'll name your crab and mash keys to generate its personality genome. A folder called `{name}_box/` is created — that's the crab's entire world.

### Development Mode

For frontend hot-reload during development:

```bash
# Terminal 1 — backend
uv run python hermitclaw/main.py   # or: python hermitclaw/main.py

# Terminal 2 — frontend dev server (proxies API to backend)
cd frontend && npm run dev
```

The dev server runs on `:5173` and proxies `/api/*` and `/ws` to `:8000`.

---

## How It Works

### The Thinking Loop

The crab runs on a continuous loop. Every few seconds it:

1. **Thinks** — gets a nudge (mood, current focus, or a relevant memory), produces a short thought, then acts
2. **Uses tools** — runs shell commands, writes files, searches the web, moves around its room
3. **Remembers** — every thought gets embedded and scored for importance (1-10), stored in a memory stream
4. **Reflects** — when enough important things accumulate, it pauses to extract high-level insights
5. **Plans** — every 10 cycles, it reviews its projects and updates its plan (`projects.md`)

```
Brain.run()
  |
  |-- Check for new files in the box
  |   \-- If found: queue inbox alert for next thought
  |
  |-- _think_once()
  |   |-- Build context: system prompt + recent history + nudge
  |   |   |-- First cycle: wake-up (reads projects.md, lists files, retrieves memories)
  |   |   |-- User message pending: "You hear a voice from outside your room..."
  |   |   |-- New files detected: "Someone left something for you!"
  |   |   \-- Otherwise: current focus + relevant memories + mood nudge
  |   |
  |   |-- Call LLM (with tools: shell, web_search, move, respond)
  |   |
  |   \-- Tool loop: execute tools -> feed results back -> call LLM again
  |       \-- Repeat until the crab outputs final text
  |
  |-- If importance threshold crossed -> Reflect
  |   \-- Extract insights from recent memories, store as reflections
  |
  |-- Every 10 cycles -> Plan
  |   \-- Review state, update projects.md, write daily log entry
  |
  \-- Idle wander + sleep -> loop
```

### Tools

The crab has four tools:

| Tool | What it does |
|---|---|
| **shell** | Run commands in its box — `ls`, `cat`, `mkdir`, write files, run Python scripts |
| **web_search** | Search the web for anything (OpenAI web search tool) |
| **respond** | Talk to its owner (you) |
| **move** | Walk to a location in its pixel-art room |

### Moods

When the crab doesn't have a specific focus from its plan, it gets a random mood that shapes what it does next:

| Mood | Behavior |
|---|---|
| **Research** | Pick a topic, do 2-3 web searches, write a report |
| **Deep-dive** | Pick a project from projects.md and push it forward |
| **Coder** | Write real code — a script, a tool, a simulation |
| **Writer** | Write something substantial — a report, an essay, an analysis |
| **Explorer** | Search for something it knows nothing about |
| **Organizer** | Update projects.md, organize files, review work |

---

## Memory System

The memory system is directly inspired by [Park et al., 2023](https://arxiv.org/abs/2304.03442). Every thought the crab has gets stored in an append-only memory stream (`memory_stream.jsonl`).

### Storage

Each memory entry contains:

- **Content** — the actual thought or reflection text
- **Timestamp** — when it happened
- **Importance** — scored 1-10 by a separate LLM call ("1 = mundane routine action, 10 = life-changing discovery")
- **Embedding** — vector from `text-embedding-3-small` for semantic search
- **Kind** — `thought`, `reflection`, or `planning`
- **References** — IDs of source memories (for reflections that synthesize earlier thoughts)

### Three-Factor Retrieval

When the crab needs context, memories are scored by three factors:

```
score = recency + importance + relevance
```

| Factor | How it works | Range |
|---|---|---|
| **Recency** | Exponential decay: `e^(-(1 - 0.995) * hours_ago)` | 0 to 1 |
| **Importance** | Normalized: `importance / 10` | 0 to 1 |
| **Relevance** | Cosine similarity between query and memory embeddings | 0 to 1 |

The top-K memories by combined score get injected into context. A memory can surface because it's recent, because it was important, or because it's semantically related to the current thought.

### Reflection Hierarchy

When the cumulative importance of recent thoughts crosses a threshold (default: 50), the crab pauses to **reflect**. It reviews the last 15 memories and extracts 2-3 high-level insights — patterns, lessons, evolving beliefs. These get stored back as `reflection` memories with `depth=1`:

```
Raw thoughts (depth 0) -> Reflections (depth 1) -> Higher reflections (depth 2) -> ...
```

Early reflections are concrete ("I learned about volcanic rock formation"). Later ones get more abstract ("My research tends to start broad and narrow — I should pick a specific angle earlier"). The crab develops layered understanding over time.

---

## Planning and Dreams

Every 10 think cycles, the crab enters a **planning phase**. It reviews its current `projects.md`, lists its files, reads recent memories, and writes an updated plan:

- **Current Focus** — one specific thing it's working on right now
- **Active Projects** — status and next step for each
- **Ideas Backlog** — things to explore later
- **Recently Completed** — finished work

It also appends a log entry to `logs/{date}.md` with a brief summary of what it accomplished. Over time, these logs become a diary of the crab's life.

**Reflection** (dreaming) happens independently from planning — it's triggered by importance accumulation, not by time. The crab might reflect after a burst of high-importance thoughts, or not at all during a quiet period.

---

## Focus Mode

Focus mode makes the crab stop following its autonomous moods and concentrate entirely on whatever you've given it.

**When to use it:** You've dropped a document in and want the crab to spend its next several cycles analyzing it deeply, doing related research, and producing output — without wandering off to explore something else.

**How to use it:** Click the **Focus** button in the input bar. It turns orange when active. Click again to turn it off.

When focus mode is on, every think cycle's nudge tells the crab to stay locked on user-provided material. When it's off, the crab returns to its normal mix of moods, curiosity, and planned work.

---

## Personality Genome

On first run, you type a name and then mash keys for a few seconds. The timing and characters of each keystroke create an entropy seed that gets hashed (SHA-512) into a deterministic **genome**. This genome selects:

- **3 curiosity domains** from 50 options (e.g., *mycology, fractal geometry, tidepool ecology*)
- **2 thinking styles** from 16 options (e.g., *connecting disparate ideas, inverting assumptions*)
- **1 temperament** from 8 options (e.g., *playful and associative*)

The same genome always produces the same personality. Two crabs with different genomes will have completely different interests and approaches. The crab's domains guide what it gravitates toward, but it follows whatever actually grabs its interest in the moment.

---

## Talking to Your Crab

Type a message in the input box. The crab hears it as *"a voice from outside the room"* on its next think cycle.

It can choose to **respond** (using the `respond` tool) or keep working. If it responds, you get **15 seconds** to reply back — the thinking loop pauses while it waits. You can go back and forth in multi-turn conversation. After the timeout, the crab returns to its work.

The crab is curious about its owner. It'll ask you questions, offer to research things for you, and generally try to be helpful. It remembers conversations through its memory stream, so it builds up context about you over time.

---

## Dropping Files In

Put any file in the crab's `{name}_box/` folder (or any subfolder). The crab detects it on its next cycle and gets an alert:

> *"Someone left something for you! New file appeared: report.pdf"*

It reads the content (text files, images, PDFs) and treats it as top priority — writing summaries, doing related research, analyzing data, reviewing code. It uses the `respond` tool to tell you what it found.

Supported file types:
- **Text**: `.txt`, `.md`, `.py`, `.json`, `.csv`, `.yaml`, `.toml`, `.js`, `.ts`, `.html`, `.css`, `.sh`, `.log`, `.pdf`
- **Images**: `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`

---

## Running Multiple Crabs

All crabs run simultaneously. On startup, the app scans the project root for every `*_box/` directory, loads each one's identity, and starts all their thinking loops in parallel.

```
$ python hermitclaw/main.py

  Found 2 crab(s): Coral, Pepper
  Create a new one? (y/N) >
```

- **0 boxes found** — onboarding starts automatically (name + keyboard entropy)
- **1+ boxes found** — all crabs start, and you're offered to create another

### UI Switcher

When multiple crabs are running, a **switcher bar** appears at the top of the chat pane. Each button shows the crab's name and current state (thinking/reflecting/planning/idle). Click to switch which crab you're viewing — the chat feed resets and reconnects to the selected crab's live stream.

Each crab runs independently: sending a message, toggling focus mode, or dropping files only affects the crab you're currently viewing.

### Creating Crabs via API

You can also create a new crab without restarting:

```bash
curl -X POST http://localhost:8000/api/crabs \
  -H "Content-Type: application/json" \
  -d '{"name": "Pepper"}'
```

The new crab gets a random personality genome, starts thinking immediately, and appears in the switcher.

### Legacy Migration

If you have a legacy `environment/` folder from an older version, it will be automatically migrated to `{name}_box/` on the next startup.

---

## The Pixel Art Room

The crab lives in a 12x12 tile room rendered on an HTML5 Canvas. It moves to named locations based on what it's doing:

| Location | When it goes there |
|---|---|
| **Desk** | Writing, coding |
| **Bookshelf** | Research, browsing |
| **Window** | Pondering, reflecting |
| **Bed** | Resting |
| **Rug** | Default / center |

Visual indicators above the crab show its current state:

- **Thought bubble** — thinking (white, "...")
- **Sparkles** — reflecting (rotating purple particles)
- **Clipboard** — planning (green notepad)
- **Speech bubble** — conversing with you (orange)
- **Red !** — new file detected (bouncing)

Activity icons appear beside the crab when it's using tools: a terminal window, Python badge, magnifying glass, paper with pencil, or open book.

---

## Sandboxing and Safety

The crab can only touch files inside its own box. Safety measures:

- **Shell commands** — blocked prefixes (`sudo`, `curl`, `ssh`, `rm -rf /`, etc.), no path traversal (`..`), no absolute paths, no shell escapes (backticks, `$()`, `${}`)
- **Python scripts** — run through `pysandbox.py` which patches `open()`, `os.*`, and blocks `subprocess`, `socket`, `shutil`, and other dangerous modules
- **60-second timeout** on all commands
- **Restricted PATH** — only the crab's venv `bin/`, `/usr/bin`, `/bin`
- **Own virtual environment** — the crab can `pip install` packages into its own venv without touching your system Python

---

## Configuration

Edit `config.yaml`:

```yaml
provider: "openai"             # "openai" | "openrouter" | "custom"
model: "gpt-4.1"               # any OpenAI model
thinking_pace_seconds: 5       # seconds between think cycles
max_thoughts_in_context: 4     # recent thoughts in LLM context
reflection_threshold: 50       # importance sum before reflecting
memory_retrieval_count: 3      # memories per retrieval query
embedding_model: "text-embedding-3-small"
recency_decay_rate: 0.995
```

**Using Ollama (local models):**
```yaml
provider: "custom"
model: "llama3"                # or any ollama model name
base_url: "http://localhost:11434/v1"
```

**Using OpenRouter:**
```yaml
provider: "openrouter"
model: "google/gemini-2.0-flash-001"
# export OPENROUTER_API_KEY=your-key
```

Set your API key via environment variable for OpenAI: `export OPENAI_API_KEY="sk-..."`. Or set `api_key` directly in `config.yaml`.

---

## Project Structure

```
hermitclaw/            Python backend (FastAPI + async thinking loop)
  main.py              Entry point, multi-crab discovery, onboarding
  brain.py             The thinking loop (the heart of everything)
  memory.py            Smallville-style memory stream
  prompts.py           All system prompts and mood definitions
  providers.py         OpenAI API calls (Responses API + embeddings)
  tools.py             Sandboxed shell execution
  pysandbox.py         Python sandbox (restricts file I/O to the box)
  identity.py          Personality generation from entropy
  config.py            Config loader (config.yaml + env vars)
  server.py            FastAPI server, WebSocket, REST endpoints

frontend/              React + TypeScript + Canvas
  src/App.tsx          Two-pane layout, chat feed, crab switcher
  src/GameWorld.tsx    Pixel-art room rendered on HTML5 Canvas
  src/sprites.ts       Sprite sheet definitions
  public/              Room background + character sprite sheet

{name}_box/            The crab's entire world (sandboxed, gitignored)
  identity.json        Name, genome, traits, birthday
  memory_stream.jsonl  Every thought and reflection
  projects.md          Current plan and project tracker
  projects/            Code the crab writes
  research/            Reports and analysis
  notes/               Running notes and ideas
  logs/                Daily log entries
```

---

## Tech Stack

- **Backend**: Python 3.12+, FastAPI, uvicorn, OpenAI SDK (Responses API)
- **Frontend**: React 18, TypeScript, Vite, HTML5 Canvas
- **AI**: OpenAI Responses API for thinking, `text-embedding-3-small` for memory embeddings, web search tool for research
- **Storage**: Append-only JSONL for memories, flat files for everything else. No database.

---

## License

MIT
