const http = require("http");
const process = require("process");
const WebSocket = require("ws");

const API_URL = "https://openrouter.ai/api/v1/chat/completions";
const DEFAULT_MAX_STEPS = 4;
const REQUEST_TIMEOUT_MS = 45000;

function parsePort(argv) {
  const index = argv.indexOf("--port");
  const value = index >= 0 ? Number(argv[index + 1]) : 8765;
  return Number.isInteger(value) && value > 0 && value < 65536 ? value : 8765;
}

function boundedSteps(value) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed)) return DEFAULT_MAX_STEPS;
  return Math.max(1, Math.min(parsed, DEFAULT_MAX_STEPS));
}

function send(socket, payload) {
  if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(payload));
}

function parseToolCalls(message) {
  return (message.tool_calls || []).map((call) => {
    const fn = call.function || {};
    let args = {};
    try {
      args = typeof fn.arguments === "string" ? JSON.parse(fn.arguments || "{}") : (fn.arguments || {});
    } catch (_error) {
      args = {};
    }
    return {
      call_id: String(call.id || ""),
      name: String(fn.name || ""),
      arguments: args && typeof args === "object" ? args : {},
    };
  }).filter((call) => call.name);
}

async function requestOpenRouter(payload, apiKey) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(API_URL, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
        "HTTP-Referer": "https://atlclips.site",
        "X-Title": "ATL Discord Bot",
      },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    const body = await response.text();
    if (!response.ok) {
      let detail = body;
      try { detail = JSON.parse(body).error?.message || body; } catch (_error) { /* keep text */ }
      throw new Error(`OpenRouter ${response.status}: ${String(detail).slice(0, 300)}`);
    }
    return JSON.parse(body);
  } finally {
    clearTimeout(timer);
  }
}

async function waitForToolResult(socket, requestId, callId) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      cleanup();
      reject(new Error("tool result timeout"));
    }, REQUEST_TIMEOUT_MS);
    const onMessage = (raw) => {
      let event;
      try { event = JSON.parse(raw.toString()); } catch (_error) { return; }
      if (event.type !== "tool_result" || event.request_id !== requestId || event.call_id !== callId) return;
      cleanup();
      resolve(event.result);
    };
    const onClose = () => {
      cleanup();
      reject(new Error("client disconnected while waiting for tool result"));
    };
    function cleanup() {
      clearTimeout(timer);
      socket.off("message", onMessage);
      socket.off("close", onClose);
    }
    socket.on("message", onMessage);
    socket.once("close", onClose);
  });
}

async function runAgent(socket, request) {
  const requestId = String(request.request_id || "");
  const apiKey = String(request.api_key || "");
  if (!requestId || !apiKey) throw new Error("sidecar request is missing an id or API key");

  const primary = String(request.model || "");
  const fallbacks = Array.isArray(request.fallback_models) ? request.fallback_models.map(String) : [];
  const models = [...new Set([primary, ...fallbacks].filter(Boolean))];
  const messages = Array.isArray(request.messages) ? request.messages.slice() : [];
  const tools = Array.isArray(request.tools) ? request.tools : [];
  const maxSteps = boundedSteps(request.max_steps);

  for (let step = 0; step <= maxSteps; step += 1) {
    send(socket, { type: "status", request_id: requestId, step, status: "thinking" });
    const payload = {
      messages,
      temperature: 0.82,
      max_tokens: 1200,
      parallel_tool_calls: false,
    };
    if (models.length === 1) payload.model = models[0];
    else payload.models = models;
    if (tools.length && step < maxSteps) {
      payload.tools = tools;
      payload.tool_choice = "auto";
    }

    const response = await requestOpenRouter(payload, apiKey);
    const choice = response.choices?.[0];
    const assistant = choice?.message || {};
    const calls = parseToolCalls(assistant);
    if (!calls.length) {
      const text = String(assistant.content || "");
      if (text) send(socket, { type: "delta", request_id: requestId, text });
      send(socket, {
        type: "final",
        request_id: requestId,
        text,
        model: response.model || primary,
        usage: response.usage || {},
      });
      return;
    }

    if (step >= maxSteps) {
      messages.push({
        role: "user",
        content: "Tool limit reached. Answer using the verified context already available.",
      });
      continue;
    }

    messages.push(assistant);
    for (const call of calls) {
      const resultPromise = waitForToolResult(socket, requestId, call.call_id);
      send(socket, {
        type: "tool_request",
        request_id: requestId,
        call_id: call.call_id,
        name: call.name,
        arguments: call.arguments,
      });
      const result = await resultPromise;
      messages.push({
        role: "tool",
        tool_call_id: call.call_id,
        content: typeof result === "string" ? result.slice(0, 6000) : JSON.stringify(result).slice(0, 6000),
      });
    }
  }
  throw new Error("agent step limit exhausted");
}

const port = parsePort(process.argv.slice(2));
const server = http.createServer();
const sockets = new WebSocket.Server({ server });

sockets.on("connection", (socket) => {
  socket.on("message", async (raw) => {
    let request;
    try { request = JSON.parse(raw.toString()); } catch (_error) { return; }
    if (request.type !== "agent_request") return;
    try {
      await runAgent(socket, request);
    } catch (error) {
      send(socket, {
        type: "error",
        request_id: request.request_id,
        error: String(error.message || "agent failed").slice(0, 300),
      });
    }
  });
});

server.listen(port, "127.0.0.1", () => {
  process.stdout.write(`agent-sidecar listening on 127.0.0.1:${port}\n`);
});
