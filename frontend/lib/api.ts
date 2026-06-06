/**
 * HTTP client for the planner agent + concierge-voice services.
 *
 * URLs come from NEXT_PUBLIC_* env vars baked at build time (see
 * docker-compose.yml `frontend.environment`).
 */

const VOICE_WS_URL  = process.env.NEXT_PUBLIC_VOICE_WS_URL  || "ws://localhost:8000/voice";
const AGENT_BASE    = process.env.NEXT_PUBLIC_AGENT_URL     || "http://localhost:8001/agent";

export const URLS = {
  voiceWs:      VOICE_WS_URL,
  agentStream:  `${AGENT_BASE}/stream`,
  agentReset:   `${AGENT_BASE}/reset`,
};

/* ─── Agent event stream types ────────────────────────────────────────── */

export type AgentEvent =
  | { event: "state.phase";            data: { phase: "thinking" | "tool_calling" | "responding" | "done" } }
  | { event: "tool.started";           data: { name: string; args: Record<string, unknown> } }
  | { event: "tool.finished";          data: { name: string; result: unknown } }
  | { event: "agent.message_segment";  data: { segment_id: string; content: string; reasoning: string } }
  | { event: "agent.audio_chunk";      data: { segment_id: string; data: string } }
  | { event: "agent.audio_done";       data: { segment_id: string; cached?: boolean; error?: string } }
  | { event: "error";                  data: { message: string } }
  | { event: "run.complete";           data: Record<string, never> };


/** Stream a single agent turn. The promise resolves when the SSE stream ends
 *  (run.complete or error). Caller is responsible for updating Redux from
 *  the events it receives. */
export async function streamAgent(
  sessionId: string,
  message: string,
  onEvent: (ev: AgentEvent) => void,
  opts: { signal?: AbortSignal } = {},
): Promise<void> {
  const response = await fetch(URLS.agentStream, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message }),
    signal: opts.signal,
  });

  if (!response.ok || !response.body) {
    throw new Error(`agent.stream HTTP ${response.status}: ${await response.text().catch(() => "")}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      // Append + normalize CRLF → LF. sse-starlette (and many SSE servers)
      // use \r\n line endings per the SSE spec; our split looks for \n\n,
      // which would never match \r\n\r\n without this normalization.
      buffer += decoder.decode(value, { stream: true });
      buffer = buffer.replace(/\r\n/g, "\n");

      // SSE events are separated by a blank line.
      let i: number;
      while ((i = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, i);
        buffer = buffer.slice(i + 2);

        let eventName = "message";
        const dataLines: string[] = [];
        // Strip any stray \r from individual lines (defensive).
        for (const line of block.replace(/\r/g, "").split("\n")) {
          if (line.startsWith("event:")) eventName = line.slice(6).trim();
          else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
        }
        const dataStr = dataLines.join("\n");
        if (!dataStr) continue;
        try {
          const parsed = JSON.parse(dataStr);
          onEvent({ event: eventName, data: parsed } as AgentEvent);
        } catch {
          /* skip malformed payload */
        }
      }
    }
  } finally {
    try { reader.releaseLock(); } catch { /* noop */ }
  }
}


/** Drop a session's history server-side. Call after the frontend resets. */
export async function resetAgentSession(sessionId: string): Promise<void> {
  try {
    await fetch(URLS.agentReset, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });
  } catch {
    /* best effort */
  }
}
