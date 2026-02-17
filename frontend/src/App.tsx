import { useEffect, useRef, useState, useCallback } from "react";
import GameWorld, { GameWorldHandle } from "./GameWorld";

interface ApiCall {
  timestamp: string;
  instructions: string;
  input: Array<Record<string, unknown>>;
  output: Array<Record<string, unknown>>;
  is_dream?: boolean;
  is_planning?: boolean;
}

interface CrabInfo {
  id: string;
  name: string;
  state: string;
  thought_count: number;
}

type Phase = "normal" | "dream" | "planning";
type Msg = { side: "left" | "right" | "system"; text: string; phase: Phase; image?: string; isRespond?: boolean };

/**
 * Render an INPUT item — we only care about:
 *  - user messages (nudges like "Continue." or "You're awake...")
 *  - function_call_output (tool results we sent back to the model)
 * Everything else in input is accumulated history (already rendered).
 */
function renderInputItem(item: Record<string, unknown>, phase: Phase): Msg | null {
  if (item.role === "user") {
    const content = item.content;
    // Content can be a string or an array (when it includes an image)
    if (Array.isArray(content)) {
      let text = "";
      let image: string | undefined;
      for (const part of content) {
        if (!part || typeof part !== "object") continue;
        const p = part as Record<string, unknown>;
        const partType = p.type;
        if (partType === "input_text" || partType === "text") {
          text = String(p.text ?? "");
        }
        if (partType === "input_image" || partType === "image_url") {
          const raw = p.image_url;
          if (typeof raw === "string") {
            image = raw;
          } else if (raw && typeof raw === "object") {
            image = String((raw as Record<string, unknown>).url ?? "");
          }
        }
      }
      return { side: "left", text: text || "[image]", phase, image };
    }
    return { side: "left", text: content as string, phase };
  }
  if (item.type === "function_call_output") {
    const output = String(item.output ?? "");
    const match = output.match(/^They say: "([^"]+)"/);
    if (match?.[1]) return { side: "left", text: match[1], phase };
    return null;
  }
  return null;
}

/**
 * Render an OUTPUT item — everything the model returned:
 *  - message (thinking text)
 *  - function_call (tool invocation)
 *  - web_search_call
 */
function renderOutputItem(item: Record<string, unknown>, phase: Phase): Msg | null {
  if (item.type === "message") {
    const content = item.content;
    let text = "";
    if (typeof content === "string") {
      text = content;
    } else if (Array.isArray(content)) {
      text = content
        .map((c) => {
          if (!c || typeof c !== "object") return "";
          const part = c as Record<string, unknown>;
          if (typeof part.text === "string") return part.text;
          return part.type ? `[${String(part.type)}]` : "";
        })
        .filter(Boolean)
        .join("\n");
    } else if (content && typeof content === "object") {
      const part = content as Record<string, unknown>;
      if (typeof part.text === "string") text = part.text;
    }
    if (text) return { side: "right", text, phase };
    return null;
  }
  if (item.type === "function_call") {
    if (item.name === "respond") {
      try {
        const args = typeof item.arguments === "string"
          ? JSON.parse(item.arguments as string)
          : item.arguments;
        return { side: "right", text: (args as Record<string, string>).message, phase, isRespond: true };
      } catch {
        return { side: "right", text: String(item.arguments), phase, isRespond: true };
      }
    }
    let cmd: string;
    if (item.name === "shell") {
      try {
        const args = typeof item.arguments === "string"
          ? JSON.parse(item.arguments as string)
          : item.arguments;
        cmd = `$ ${(args as Record<string, string>).command}`;
      } catch {
        cmd = `$ ${item.arguments}`;
      }
    } else {
      const args = typeof item.arguments === "string"
        ? item.arguments
        : JSON.stringify(item.arguments, null, 2);
      cmd = `[${item.name}] ${args}`;
    }
    return { side: "right", text: cmd, phase };
  }
  if (item.type === "web_search_call") {
    return { side: "right", text: "[web search]", phase };
  }
  return null;
}

export default function App() {
  const [calls, setCalls] = useState<ApiCall[]>([]);
  const [position, setPosition] = useState({ x: 5, y: 5 });
  const [crabState, setCrabState] = useState("idle");
  const [alert, setAlert] = useState(false);
  const [activity, setActivity] = useState({ type: "idle", detail: "" });
  const [chatInput, setChatInput] = useState("");
  const [conversing, setConversing] = useState(false);
  const [countdown, setCountdown] = useState(0);
  const [hasNew, setHasNew] = useState(false);
  const [crabName, setCrabName] = useState("the crab");
  const [focusMode, setFocusMode] = useState(false);
  const [crabs, setCrabs] = useState<CrabInfo[]>([]);
  const [activeCrab, setActiveCrab] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const gameRef = useRef<GameWorldHandle>(null);
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const crabParam = activeCrab ? `?crab=${activeCrab}` : "";

  const connectWs = useCallback((crabId: string) => {
    // Close existing connection
    if (wsRef.current) {
      wsRef.current.onmessage = null;
      wsRef.current.onclose = null;
      wsRef.current.onerror = null;
      wsRef.current.close();
      wsRef.current = null;
    }

    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${protocol}//${location.host}/ws/${crabId}`);
    wsRef.current = ws;

    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.event === "api_call") setCalls((prev) => [...prev, msg.data]);
      if (msg.event === "position") setPosition(msg.data);
      if (msg.event === "status") {
        setCrabState(msg.data.state);
        if (msg.data.state === "thinking") setAlert(false);
      }
      if (msg.event === "alert") setAlert(true);
      if (msg.event === "activity") setActivity(msg.data);
      if (msg.event === "focus_mode") setFocusMode(msg.data.enabled);
      if (msg.event === "conversation") {
        if (msg.data.state === "waiting") {
          setConversing(true);
          setCountdown(msg.data.timeout);
        } else if (msg.data.state === "ended") {
          setConversing(false);
          setCountdown(0);
        }
      }
    };

    ws.onerror = () => {
      console.warn(`WebSocket error for crab ${crabId}`);
    };

    ws.onclose = () => {
      // Reconnect after a brief delay if this is still the active WS
      if (wsRef.current === ws) {
        setTimeout(() => {
          if (wsRef.current === ws) connectWs(crabId);
        }, 3000);
      }
    };
  }, []);

  const loadCrabState = useCallback(async (crabId: string) => {
    const q = `?crab=${crabId}`;
    // Fetch historical calls first (before WS connects) to avoid race
    try {
      const [rawRes, statusRes, idRes] = await Promise.all([
        fetch(`/api/raw${q}`),
        fetch(`/api/status${q}`),
        fetch(`/api/identity${q}`),
      ]);
      const rawData = await rawRes.json();
      const statusData = await statusRes.json();
      const idData = await idRes.json();
      setCalls(rawData);
      if (statusData.position) setPosition(statusData.position);
      if (statusData.focus_mode !== undefined) setFocusMode(statusData.focus_mode);
      setCrabState(statusData.state || "idle");
      if (idData.name) setCrabName(idData.name);
    } catch {
      // silently ignore fetch errors
    }
  }, []);

  // Initial mount: fetch crabs list, load state, then connect WS
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/crabs");
        const list: CrabInfo[] = await res.json();
        if (cancelled) return;
        setCrabs(list);
        if (list.length > 0) {
          const first = list[0].id;
          setActiveCrab(first);
          setCrabName(list[0].name);
          // Load historical data first, then connect WS for live events
          await loadCrabState(first);
          if (!cancelled) connectWs(first);
        }
      } catch { /* server not ready yet */ }
    })();

    return () => {
      cancelled = true;
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
      }
    };
  }, [connectWs, loadCrabState]);

  // Switch crab
  const switchCrab = useCallback((crabId: string) => {
    if (crabId === activeCrab) return;
    setActiveCrab(crabId);

    // Reset state
    setConversing(false);
    setCountdown(0);
    setAlert(false);
    setActivity({ type: "idle", detail: "" });
    setHasNew(false);

    // Update crab name immediately
    const crab = crabs.find((c) => c.id === crabId);
    if (crab) setCrabName(crab.name);

    // Load historical data first, then connect WS for live events
    loadCrabState(crabId).then(() => connectWs(crabId));
  }, [activeCrab, crabs, loadCrabState, connectWs]);

  // Poll crabs list periodically to keep states fresh
  useEffect(() => {
    const interval = setInterval(() => {
      fetch("/api/crabs")
        .then((r) => r.json())
        .then(setCrabs)
        .catch(() => {});
    }, 5000);
    return () => clearInterval(interval);
  }, []);

  // Countdown timer for conversation window
  useEffect(() => {
    if (countdownRef.current) clearInterval(countdownRef.current);
    if (countdown > 0) {
      countdownRef.current = setInterval(() => {
        setCountdown((c) => {
          if (c <= 1) {
            clearInterval(countdownRef.current!);
            return 0;
          }
          return c - 1;
        });
      }, 1000);
    }
    return () => { if (countdownRef.current) clearInterval(countdownRef.current); };
  }, [conversing]);

  // Send canvas snapshot to backend when thinking starts
  useEffect(() => {
    if (crabState === "thinking" && gameRef.current) {
      const dataUrl = gameRef.current.snapshot();
      if (dataUrl) {
        fetch(`/api/snapshot${crabParam}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ image: dataUrl }),
        }).catch(() => {});
      }
    }
  }, [crabState, crabParam]);

  // Only auto-scroll if user is already near the bottom; otherwise show indicator
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 150;
    if (nearBottom) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    } else {
      setHasNew(true);
    }
  }, [calls.length]);

  // Clear "new messages" when user scrolls to bottom
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 150;
      if (nearBottom) setHasNew(false);
    };
    el.addEventListener("scroll", onScroll);
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  const sendMessage = () => {
    const text = chatInput.trim();
    if (!text) return;
    fetch(`/api/message${crabParam}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }).catch(() => {});
    setChatInput("");
  };

  const toggleFocusMode = () => {
    const next = !focusMode;
    setFocusMode(next);
    fetch(`/api/focus-mode${crabParam}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: next }),
    }).catch(() => {});
  };

  // Build a deduplicated conversation stream.
  // Each API call's input contains the FULL accumulated history.
  // We only render NEW items in each call's input (items we haven't seen yet)
  // plus all output items (what the model returned).
  const messages: Msg[] = [];
  let seenInputItems = 0;

  calls.forEach((call, i) => {
    const isDream = call.is_dream ?? false;
    const isPlanning = call.is_planning ?? false;
    const phase: Phase = isDream ? "dream" : isPlanning ? "planning" : "normal";

    // System prompt (first call or when instructions meaningfully changed) — skip for dream/planning calls
    const strip = (s: string) => s.replace(/Right now it is .+\n/, "").replace(/## Current (mood|focus)\n[\s\S]*?(?=\n##)/, "");
    if (!isDream && !isPlanning && (i === 0 || strip(call.instructions) !== strip(calls[i - 1]?.instructions ?? ""))) {
      messages.push({ side: "system", text: call.instructions, phase: "normal" });
    }

    // Dream divider
    if (isDream && (i === 0 || !calls[i - 1]?.is_dream)) {
      messages.push({ side: "system", text: "Reflecting...", phase: "dream" });
    }

    // Planning divider
    if (isPlanning && (i === 0 || !calls[i - 1]?.is_planning)) {
      messages.push({ side: "system", text: "Planning...", phase: "planning" });
    }

    // If input didn't grow (rebuilt from scratch for a new think cycle), reset.
    // Accumulated tool-loop inputs always grow strictly (new function_call_outputs),
    // so equal-or-smaller means the input was rebuilt by _build_input().
    if (seenInputItems >= call.input.length) {
      seenInputItems = 0;
    }

    // Only render NEW input items (skip already-rendered history)
    const newInputs = call.input.slice(seenInputItems);
    for (const item of newInputs) {
      const msg = renderInputItem(item, phase);
      if (msg) messages.push(msg);
    }

    // Render all output items (what the model returned this call)
    for (const item of call.output) {
      const msg = renderOutputItem(item, phase);
      if (msg) messages.push(msg);
    }

    // Track how many items the next call's input will start with
    seenInputItems = call.input.length + call.output.length;
  });

  const stateLabel = (state: string) => {
    if (state === "thinking") return "thinking";
    if (state === "reflecting") return "reflecting";
    if (state === "planning") return "planning";
    return "idle";
  };

  const stateColor = (state: string) => {
    if (state === "thinking") return "#007aff";
    if (state === "reflecting") return "#7c3aed";
    if (state === "planning") return "#0d9488";
    return "#999";
  };

  return (
    <div style={page}>
      <div style={headerBar}>
        <img src="/icon.png" alt="HermitClaw" style={headerIcon} />
        <span style={headerTitle}>HermitClaw</span>
      </div>
      <div style={twoPane}>
        {/* Left pane — Game world */}
        <div style={gamePane}>
          <GameWorld ref={gameRef} position={position} state={crabState} alert={alert} activity={activity} conversing={conversing} />
        </div>

        {/* Right pane — Chat feed */}
        <div style={chatPane}>
          {/* Crab switcher */}
          {crabs.length > 1 && (
            <div style={switcherBar}>
              {crabs.map((c) => {
                const isActive = c.id === activeCrab;
                return (
                  <button
                    key={c.id}
                    style={isActive ? switcherBtnActive : switcherBtnInactive}
                    onClick={() => switchCrab(c.id)}
                  >
                    <span>{c.name}</span>
                    <span style={{ ...switcherState, color: isActive ? "rgba(255,255,255,0.8)" : stateColor(c.state) }}>
                      {stateLabel(c.state)}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
          <div ref={scrollRef} style={chatScroll}>
          <div style={container}>
            {messages.length === 0 && (
              <div style={emptyState}>
                <div style={emptyIcon}>~</div>
                <div style={emptyTitle}>Waiting for thoughts...</div>
                <div style={emptySubtitle}>{crabName} is getting ready</div>
              </div>
            )}
            {messages.map((msg, i) => {
              if (msg.side === "system") {
                const sBlock = msg.phase === "dream" ? dreamSystemBlock
                  : msg.phase === "planning" ? planSystemBlock : systemBlock;
                const sLabel = msg.phase === "dream" ? dreamSystemLabel
                  : msg.phase === "planning" ? planSystemLabel : systemLabel;
                const sText = msg.phase === "dream" ? dreamSystemText
                  : msg.phase === "planning" ? planSystemText : systemText;
                const label = msg.phase === "dream" ? "Reflection"
                  : msg.phase === "planning" ? "Planning" : "System Prompt";
                return (
                  <div key={i} style={sBlock}>
                    <div style={sLabel}>{label}</div>
                    <pre style={sText}>{msg.text}</pre>
                  </div>
                );
              }

              const isLeft = msg.side === "left";
              const p = msg.phase;

              const bubbleStyle = msg.isRespond
                ? respondBubble
                : p === "dream"
                ? isLeft ? dreamBubbleLeft : dreamBubbleRight
                : p === "planning"
                ? isLeft ? planBubbleLeft : planBubbleRight
                : isLeft ? bubbleLeft : bubbleRight;

              const textColor = isLeft && p === "normal" && !msg.isRespond ? "#111" : "#fff";

              return (
                <div
                  key={i}
                  style={{
                    display: "flex",
                    justifyContent: isLeft ? "flex-start" : "flex-end",
                    marginBottom: 6,
                  }}
                >
                  <div style={bubbleStyle}>
                    {msg.image && (
                      <img
                        src={msg.image}
                        style={snapshotImg}
                        alt="Room snapshot"
                      />
                    )}
                    <pre style={{ ...bubbleText, color: textColor }}>
                      {msg.text}
                    </pre>
                  </div>
                </div>
              );
            })}
            <div ref={bottomRef} />
          </div>
          </div>
          {hasNew && (
            <div
              style={newMsgPill}
              onClick={() => {
                bottomRef.current?.scrollIntoView({ behavior: "smooth" });
                setHasNew(false);
              }}
            >
              New messages
            </div>
          )}
          <div style={inputBar}>
            {conversing && countdown > 0 && (
              <div style={countdownStyle}>{countdown}s</div>
            )}
            <button
              style={focusMode ? focusBtnActive : focusBtnInactive}
              onClick={toggleFocusMode}
              title={focusMode ? "Focus mode ON — click to turn off" : "Focus mode OFF — click to turn on"}
            >
              Focus
            </button>
            <form
              style={inputForm}
              onSubmit={(e) => { e.preventDefault(); sendMessage(); }}
            >
              <input
                style={inputField}
                type="text"
                placeholder={conversing ? `Reply to ${crabName}...` : `Say something to ${crabName}...`}
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
              />
              <button style={sendBtn} type="submit">Send</button>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Shared palette ──
const DARK = "#0f0f1a";
const DARK_MID = "#1a1a2e";
const DARK_BORDER = "#2a2a4a";
const SURFACE = "#f4f4f8";
const BORDER = "#e2e2ea";
const MONO = "'SF Mono', 'Fira Code', 'Cascadia Code', Consolas, monospace";
const SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";

const page: React.CSSProperties = {
  background: DARK,
  color: "#111",
  fontFamily: SANS,
  height: "100vh",
  overflow: "hidden",
  display: "flex",
  flexDirection: "column",
};

// ── Header ──
const headerBar: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 16,
  padding: "10px 24px",
  background: DARK,
  borderBottom: `1px solid ${DARK_BORDER}`,
  flexShrink: 0,
};

const headerIcon: React.CSSProperties = {
  maxHeight: 48,
};

const headerTitle: React.CSSProperties = {
  fontSize: 24,
  fontWeight: 700,
  color: "#fff",
  whiteSpace: "nowrap",
  letterSpacing: "-0.3px",
};

// ── Layout ──
const twoPane: React.CSSProperties = {
  display: "flex",
  flex: 1,
  overflow: "hidden",
};

const gamePane: React.CSSProperties = {
  width: "45%",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  background: DARK_MID,
  padding: 20,
  flexShrink: 0,
};

const chatPane: React.CSSProperties = {
  width: "55%",
  height: "100%",
  display: "flex",
  flexDirection: "column",
  background: SURFACE,
  borderLeft: `1px solid ${BORDER}`,
};

const chatScroll: React.CSSProperties = {
  flex: 1,
  overflow: "auto",
};

const container: React.CSSProperties = {
  maxWidth: 720,
  margin: "0 auto",
  padding: "24px 20px",
};

// ── Empty state ──
const emptyState: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  padding: "80px 20px",
  gap: 8,
};

const emptyIcon: React.CSSProperties = {
  fontSize: 32,
  color: "#c4c4d0",
  fontFamily: MONO,
};

const emptyTitle: React.CSSProperties = {
  fontSize: 16,
  fontWeight: 600,
  color: "#8888a0",
  letterSpacing: "-0.2px",
};

const emptySubtitle: React.CSSProperties = {
  fontSize: 13,
  color: "#aaa",
};

// ── Crab switcher ──
const switcherBar: React.CSSProperties = {
  display: "flex",
  gap: 6,
  padding: "8px 16px",
  borderBottom: `1px solid ${BORDER}`,
  background: "#fff",
  overflowX: "auto",
  flexShrink: 0,
};

const switcherBtnBase: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  gap: 2,
  padding: "6px 16px",
  borderRadius: 8,
  border: `1px solid ${BORDER}`,
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
  whiteSpace: "nowrap",
  transition: "all 0.15s",
  background: "transparent",
};

const switcherBtnActive: React.CSSProperties = {
  ...switcherBtnBase,
  background: DARK_MID,
  color: "#fff",
  borderColor: DARK_MID,
};

const switcherBtnInactive: React.CSSProperties = {
  ...switcherBtnBase,
  background: "#fff",
  color: "#555",
};

const switcherState: React.CSSProperties = {
  fontSize: 10,
  fontWeight: 500,
  textTransform: "uppercase",
  letterSpacing: "0.4px",
};

// ── Chat bubbles ──
const bubbleBase: React.CSSProperties = {
  padding: "10px 16px",
  maxWidth: "78%",
  boxShadow: "0 1px 2px rgba(0,0,0,0.06)",
};

const bubbleLeft: React.CSSProperties = {
  ...bubbleBase,
  background: "#fff",
  borderRadius: "16px 16px 16px 4px",
  border: `1px solid ${BORDER}`,
};

const bubbleRight: React.CSSProperties = {
  ...bubbleBase,
  background: DARK_MID,
  color: "#fff",
  borderRadius: "16px 16px 4px 16px",
};

const dreamBubbleLeft: React.CSSProperties = {
  ...bubbleBase,
  background: "#7c3aed",
  borderRadius: "16px 16px 16px 4px",
};

const dreamBubbleRight: React.CSSProperties = {
  ...bubbleBase,
  background: "#6d28d9",
  color: "#fff",
  borderRadius: "16px 16px 4px 16px",
};

const planBubbleLeft: React.CSSProperties = {
  ...bubbleBase,
  background: "#0d9488",
  borderRadius: "16px 16px 16px 4px",
};

const planBubbleRight: React.CSSProperties = {
  ...bubbleBase,
  background: "#0f766e",
  color: "#fff",
  borderRadius: "16px 16px 4px 16px",
};

const respondBubble: React.CSSProperties = {
  ...bubbleBase,
  background: "#ea580c",
  color: "#fff",
  borderRadius: "16px 16px 4px 16px",
};

const snapshotImg: React.CSSProperties = {
  width: "100%",
  maxWidth: 200,
  borderRadius: 8,
  marginBottom: 6,
  imageRendering: "pixelated",
};

const bubbleText: React.CSSProperties = {
  margin: 0,
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  fontFamily: MONO,
  fontSize: 12.5,
  lineHeight: "1.6",
};

// ── System blocks ──
const systemBlock: React.CSSProperties = {
  background: "#fff",
  borderRadius: 10,
  padding: "14px 18px",
  marginBottom: 16,
  border: `1px solid ${BORDER}`,
};

const systemLabel: React.CSSProperties = {
  fontSize: 10,
  fontWeight: 700,
  color: "#aaa",
  textTransform: "uppercase",
  marginBottom: 8,
  letterSpacing: "0.8px",
};

const systemText: React.CSSProperties = {
  margin: 0,
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  fontSize: 12,
  lineHeight: "1.6",
  color: "#555",
  fontFamily: MONO,
};

const dreamSystemBlock: React.CSSProperties = {
  ...systemBlock,
  background: "#faf5ff",
  borderColor: "#ddd6fe",
};

const dreamSystemLabel: React.CSSProperties = {
  ...systemLabel,
  color: "#7c3aed",
};

const dreamSystemText: React.CSSProperties = {
  ...systemText,
  color: "#5b21b6",
};

const planSystemBlock: React.CSSProperties = {
  ...systemBlock,
  background: "#f0fdfa",
  borderColor: "#a7f3d0",
};

const planSystemLabel: React.CSSProperties = {
  ...systemLabel,
  color: "#0d9488",
};

const planSystemText: React.CSSProperties = {
  ...systemText,
  color: "#115e59",
};

// ── Input bar ──
const inputBar: React.CSSProperties = {
  borderTop: `1px solid ${BORDER}`,
  padding: "12px 20px",
  background: "#fff",
  display: "flex",
  alignItems: "center",
  gap: 10,
};

const inputForm: React.CSSProperties = {
  display: "flex",
  flex: 1,
  gap: 10,
};

const inputField: React.CSSProperties = {
  flex: 1,
  padding: "10px 16px",
  borderRadius: 10,
  border: `1px solid ${BORDER}`,
  fontSize: 13,
  fontFamily: MONO,
  outline: "none",
  background: SURFACE,
  color: "#333",
};

const sendBtn: React.CSSProperties = {
  padding: "10px 20px",
  borderRadius: 10,
  border: "none",
  background: DARK_MID,
  color: "#fff",
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
  letterSpacing: "0.2px",
};

const countdownStyle: React.CSSProperties = {
  fontSize: 13,
  fontWeight: 700,
  color: "#ea580c",
  fontFamily: MONO,
  minWidth: 30,
};

const focusBtnInactive: React.CSSProperties = {
  padding: "8px 14px",
  borderRadius: 10,
  border: `1px solid ${BORDER}`,
  background: SURFACE,
  color: "#999",
  fontSize: 12,
  fontWeight: 600,
  cursor: "pointer",
  whiteSpace: "nowrap",
};

const focusBtnActive: React.CSSProperties = {
  padding: "8px 14px",
  borderRadius: 10,
  border: "1px solid #ea580c",
  background: "#ea580c",
  color: "#fff",
  fontSize: 12,
  fontWeight: 600,
  cursor: "pointer",
  whiteSpace: "nowrap",
};

const newMsgPill: React.CSSProperties = {
  textAlign: "center",
  padding: "8px 0",
  background: DARK_MID,
  color: "#fff",
  fontSize: 12,
  fontWeight: 600,
  cursor: "pointer",
  letterSpacing: "0.4px",
};
