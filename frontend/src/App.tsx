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
        if (part.type === "input_text") text = part.text as string;
        if (part.type === "input_image") image = part.image_url as string;
      }
      return { side: "left", text: text || "[image]", phase, image };
    }
    return { side: "left", text: content as string, phase };
  }
  if (item.type === "function_call_output") {
    return { side: "left", text: item.output as string, phase };
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
    const content = item.content as Array<Record<string, unknown>>;
    const text = content
      ?.map((c) => (c.text as string) || `[${c.type}]`)
      .join("\n");
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

const page: React.CSSProperties = {
  background: "#fff",
  color: "#111",
  fontFamily:
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
  height: "100vh",
  overflow: "hidden",
  display: "flex",
  flexDirection: "column",
};

const headerBar: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "6px 16px",
  background: "#1a1a2e",
  borderBottom: "1px solid #2a2a4a",
  flexShrink: 0,
};

const headerIcon: React.CSSProperties = {
  width: 28,
  height: 28,
  borderRadius: 6,
};

const headerTitle: React.CSSProperties = {
  fontSize: 15,
  fontWeight: 700,
  color: "#e2e8f0",
  letterSpacing: "0.3px",
};

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
  background: "#1a1a2e",
  padding: 16,
  flexShrink: 0,
};

const chatPane: React.CSSProperties = {
  width: "55%",
  height: "100%",
  display: "flex",
  flexDirection: "column",
};

const chatScroll: React.CSSProperties = {
  flex: 1,
  overflow: "auto",
};

const container: React.CSSProperties = {
  maxWidth: 800,
  margin: "0 auto",
  padding: "20px 16px",
};

const switcherBar: React.CSSProperties = {
  display: "flex",
  gap: 4,
  padding: "6px 12px",
  borderBottom: "1px solid #e0e0e0",
  background: "#fafafa",
  overflowX: "auto",
  flexShrink: 0,
};

const switcherBtnBase: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  gap: 2,
  padding: "6px 14px",
  borderRadius: 8,
  border: "1px solid #d4d4d8",
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
  whiteSpace: "nowrap",
  transition: "all 0.15s",
};

const switcherBtnActive: React.CSSProperties = {
  ...switcherBtnBase,
  background: "#007aff",
  color: "#fff",
  borderColor: "#007aff",
};

const switcherBtnInactive: React.CSSProperties = {
  ...switcherBtnBase,
  background: "#fff",
  color: "#333",
};

const switcherState: React.CSSProperties = {
  fontSize: 10,
  fontWeight: 500,
  textTransform: "uppercase",
  letterSpacing: "0.3px",
};

const bubbleLeft: React.CSSProperties = {
  background: "#e9e9eb",
  borderRadius: "18px 18px 18px 4px",
  padding: "8px 14px",
  maxWidth: "75%",
};

const bubbleRight: React.CSSProperties = {
  background: "#007aff",
  color: "#fff",
  borderRadius: "18px 18px 4px 18px",
  padding: "8px 14px",
  maxWidth: "75%",
};

// Dream bubble styles — purple theme
const dreamBubbleLeft: React.CSSProperties = {
  background: "#6d28d9",
  borderRadius: "18px 18px 18px 4px",
  padding: "8px 14px",
  maxWidth: "75%",
};

const dreamBubbleRight: React.CSSProperties = {
  background: "#7c3aed",
  color: "#fff",
  borderRadius: "18px 18px 4px 18px",
  padding: "8px 14px",
  maxWidth: "75%",
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
  fontFamily: "'SF Mono', 'Fira Code', Consolas, monospace",
  fontSize: 13,
  lineHeight: "1.5",
};

const systemBlock: React.CSSProperties = {
  background: "#f5f5f5",
  borderRadius: 12,
  padding: "12px 16px",
  marginBottom: 16,
  border: "1px solid #e0e0e0",
};

const systemLabel: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 700,
  color: "#999",
  textTransform: "uppercase",
  marginBottom: 6,
  letterSpacing: "0.5px",
};

const systemText: React.CSSProperties = {
  margin: 0,
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  fontSize: 12,
  lineHeight: "1.5",
  color: "#555",
  fontFamily: "'SF Mono', 'Fira Code', Consolas, monospace",
};

// Dream system block — purple divider
const dreamSystemBlock: React.CSSProperties = {
  background: "#f5f3ff",
  borderRadius: 12,
  padding: "12px 16px",
  marginBottom: 16,
  border: "1px solid #c4b5fd",
};

const dreamSystemLabel: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 700,
  color: "#7c3aed",
  textTransform: "uppercase",
  marginBottom: 6,
  letterSpacing: "0.5px",
};

const dreamSystemText: React.CSSProperties = {
  margin: 0,
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  fontSize: 12,
  lineHeight: "1.5",
  color: "#6d28d9",
  fontFamily: "'SF Mono', 'Fira Code', Consolas, monospace",
};

// Planning bubble styles — green/teal theme
const planBubbleLeft: React.CSSProperties = {
  background: "#0d9488",
  borderRadius: "18px 18px 18px 4px",
  padding: "8px 14px",
  maxWidth: "75%",
};

const planBubbleRight: React.CSSProperties = {
  background: "#14b8a6",
  color: "#fff",
  borderRadius: "18px 18px 4px 18px",
  padding: "8px 14px",
  maxWidth: "75%",
};

// Planning system block — green divider
const planSystemBlock: React.CSSProperties = {
  background: "#f0fdfa",
  borderRadius: 12,
  padding: "12px 16px",
  marginBottom: 16,
  border: "1px solid #99f6e4",
};

const planSystemLabel: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 700,
  color: "#0d9488",
  textTransform: "uppercase",
  marginBottom: 6,
  letterSpacing: "0.5px",
};

const planSystemText: React.CSSProperties = {
  margin: 0,
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  fontSize: 12,
  lineHeight: "1.5",
  color: "#115e59",
  fontFamily: "'SF Mono', 'Fira Code', Consolas, monospace",
};

// Respond bubble — speech from crab to user (orange/warm)
const respondBubble: React.CSSProperties = {
  background: "#ea580c",
  color: "#fff",
  borderRadius: "18px 18px 4px 18px",
  padding: "8px 14px",
  maxWidth: "75%",
};

// Input bar styles
const inputBar: React.CSSProperties = {
  borderTop: "1px solid #e0e0e0",
  padding: "8px 16px",
  background: "#fafafa",
  display: "flex",
  alignItems: "center",
  gap: 8,
};

const inputForm: React.CSSProperties = {
  display: "flex",
  flex: 1,
  gap: 8,
};

const inputField: React.CSSProperties = {
  flex: 1,
  padding: "8px 12px",
  borderRadius: 20,
  border: "1px solid #d4d4d8",
  fontSize: 14,
  fontFamily: "'SF Mono', 'Fira Code', Consolas, monospace",
  outline: "none",
};

const sendBtn: React.CSSProperties = {
  padding: "8px 16px",
  borderRadius: 20,
  border: "none",
  background: "#007aff",
  color: "#fff",
  fontSize: 14,
  fontWeight: 600,
  cursor: "pointer",
};

const countdownStyle: React.CSSProperties = {
  fontSize: 13,
  fontWeight: 700,
  color: "#ea580c",
  fontFamily: "monospace",
  minWidth: 30,
};

const focusBtnInactive: React.CSSProperties = {
  padding: "6px 12px",
  borderRadius: 20,
  border: "1px solid #d4d4d8",
  background: "#fff",
  color: "#999",
  fontSize: 12,
  fontWeight: 600,
  cursor: "pointer",
  whiteSpace: "nowrap",
};

const focusBtnActive: React.CSSProperties = {
  padding: "6px 12px",
  borderRadius: 20,
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
  padding: "6px 0",
  background: "#007aff",
  color: "#fff",
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
  letterSpacing: "0.3px",
};
