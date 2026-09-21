# AI reply smoke test

Run these checks after deploying a new AI build:

1. Mention the bot with a greeting. It should answer once and remain responsive.
2. Reply to the bot from a different accessible channel. It should answer using the current channel context.
3. In the general memory channel, have Joel, Polos, and proxic send nearby messages. Ask who wrote the message above. The answer should name the preceding Discord author, not a mentioned user.
4. Tell the bot `I'm not CherryBomb`, then send a follow-up from the same user. The correction should apply briefly to that user only.
5. Ask for a rank. The reply should use verified rank data or say the lookup failed; it must not infer a rank from lore.
6. Ask about an image in a channel the requester cannot view. The bot must reject the search without reading that channel's history.
7. Temporarily make Gemini unavailable. The bot should send one short failure response and stop typing instead of hanging.

Check logs for stage durations and message IDs only. They must not contain message content or API keys.
