# Agent sidecar

This is the Archimedes-style agent runtime for the Discord bot. It owns the
bounded OpenRouter reasoning loop, while the Python process remains the only
authority allowed to execute Discord/database tools.

Protocol:

- Python sends `agent_request` with messages and JSON-schema tool definitions.
- The sidecar sends `tool_request` events and waits for Python `tool_result`
  events.
- The sidecar emits `status`, `delta`, `final`, or `error` events.
- The step count is clamped to four on both sides.

Install the sidecar dependency once with `npm install` in this directory. If
the dependency is unavailable, the bot automatically uses its in-process
OpenRouter loop through the same Python tool pipeline.
