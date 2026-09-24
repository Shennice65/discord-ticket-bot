# Context JSON v2 Refactor Design

## Purpose
The AI Context JSON currently duplicates conversation history. Messages often appear simultaneously in `recent_channel_messages`, `recent_messages`, and `exchanges` (which are passed as conversational turns). This causes token waste (500-800 tokens per request) and degrades AI accuracy by duplicating the timeline. This refactor eliminates duplication and slims down the JSON payload.

## Architecture & Data Flow

1. **Remove `recent_messages` entirely**
   - The `recent_messages` field in the Context JSON overlaps heavily with `recent_channel_messages`.
   - We will drop it from the `BrainContext` dataclass and from `context_text()`.
   - `recent_channel_messages` will be renamed to just `recent_messages` in the JSON output, backed by the `surrounding_messages` (live window) from the tracker.

2. **Deduplicate JSON vs. Conversational Turns (`exchanges`)**
   - In `router.py`, the AI is fed `exchanges` as actual conversational turns (`role: "user"`, `role: "assistant"`).
   - If a message ID exists in the `exchanges` that are being sent to the AI, it MUST NOT be included in the JSON `recent_messages` array.
   - We will filter `live` messages in `router.py` (or `prompts.py`) to exclude any message ID that is already present in the active `exchanges`.

3. **Trim Redundant JSON Metadata**
   - In `prompts.py` -> `message_data()`:
     - Remove `created_at` (the AI doesn't need exact ISO timestamps for chat flow).
     - Remove `mentioned_user_ids` (redundant, as `mentioned_users` already includes `id` and `name`).
     - Cap message `content` to 200 characters (down from 500) since this is just background context, not the main conversational turns.
   
4. **Remove Dead Code**
   - Delete `AgentSidecarBridge` from `ai/sidecar.py` since it is locked to the "openrouter" provider and unused in the current DeepSeek/Gemini architecture.
   - Remove references to the sidecar from `ai/router.py`.

## Testing
- Ensure tests in `tests/test_chat_context.py` and `tests/test_openrouter_agent.py` pass.
- Update test mocks to remove `recent_messages` from `BrainContext` instantiation.

## Success Criteria
- The JSON block no longer contains messages that are already present in the conversational turns.
- Total system prompt size (excluding current message) is reduced by ~500 tokens.
- No `created_at` timestamps or `mentioned_user_ids` fields in the generated JSON.
