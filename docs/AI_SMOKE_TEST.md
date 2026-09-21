# AI reply smoke test

Run these checks after deploying a new AI build:

1. Mention the bot with a greeting. It should answer once and remain responsive.
2. Reply to the bot from a different accessible channel. It should answer using the current channel context.
3. In the general memory channel, have Joel, Polos, and proxic send nearby messages. Ask who wrote the message above. The answer should name the preceding Discord author, not a mentioned user.
4. Tell the bot `I'm not CherryBomb`, then send a follow-up from the same user. The correction should apply briefly to that user only.
5. Ask for a rank. The reply should use verified rank data or say the lookup failed; it must not infer a rank from lore.
6. Ask about an image in a channel the requester cannot view. The bot must reject the search without reading that channel's history.
7. Temporarily make Gemini unavailable. The bot should send one short failure response and stop typing instead of hanging.

8. Ask for a current rank, player history, leaderboard, ticket history, server rules, lore, and clips. The bot should use read-only tools and never mutate server or database state.

9. Trigger a tool-call failure or an unavailable tool. The bot should explain that the lookup is unavailable and still finish the response without exposing provider or database details.

10. Gemini mode uses the Python agent runner. If OpenRouter is enabled later,
install `agent-sidecar` dependencies and verify that stopping the sidecar
transparently falls back to the same Python tool pipeline.

11. Verify the reset utility in a test database with
`python scripts/reset_ai_memory.py --confirm-ai-reset`. It may delete only
`chat_memory`, `chat_messages`, and `pending_lore`; ranking, ticket, betting,
clip, settings, and Discord-message data must remain unchanged.

The sidecar setup is:

```text
cd agent-sidecar
npm install
```

Check logs for stage durations and message IDs only. They must not contain message content or API keys.
