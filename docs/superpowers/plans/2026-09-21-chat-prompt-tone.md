# Chat Prompt Tone Adjustment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update the bot's system instruction to enforce a nonchalant, concise tone that avoids trying too hard to roast and reduces the frequency of mentioning the "novice" rank.

**Architecture:** Modify the SYSTEM_INSTRUCTION string in `core/services/chat_prompts.py` to change the behavioral guidelines for the bot.

**Tech Stack:** Python

## Global Constraints

None specified.

---

### Task 1: Update Banter and Tone Guidelines

**Files:**
- Modify: `core/services/chat_prompts.py`

**Interfaces:**
- Consumes: None
- Produces: Updated SYSTEM_INSTRUCTION string.

- [ ] **Step 1: Write the failing test**

*(No automated test required for prompt string tuning, but we can verify by checking the string content in Python shell)*

```python
# No formal test needed for string edit, but we ensure it compiles.
```

- [ ] **Step 2: Run test to verify it fails**

*(Skip)*

- [ ] **Step 3: Write minimal implementation**

Update `core/services/chat_prompts.py` around line 20:

Change:
```python
    "Be highly unpredictable. Randomly choose to either: ruthlessly roast them back, hit them with a 'womp womp', act completely confused about who they are, or sarcastically agree with them. Never respond to insults the same way twice.\n"
    "Whenever you make jokes, analogies, or insults, ALWAYS root them in the specific terminology provided in your lore. Do NOT use generic internet/gaming tropes (e.g. if roasting skill, use the specific server ranks provided instead of 'bronze'). You are an exclusive member of THIS specific server, so use its unique culture.\n\n"
```

To:
```python
    "Be nonchalant, concise, and unbothered. Your words should carry weight; do not try too hard to roast or argue. Keep responses short and impactful. Never respond to insults the same way twice.\n"
    "When referencing server culture, vary your terminology. Do NOT overuse the term 'novice' or constantly mention low ranks. Do NOT use generic internet/gaming tropes. You are an exclusive member of THIS specific server, so use its unique culture naturally.\n\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m py_compile core/services/chat_prompts.py`
Expected: No syntax errors

- [ ] **Step 5: Commit**

```bash
git add core/services/chat_prompts.py
git commit -m "Update chat prompts: change tone to nonchalant and concise, reduce novice mentions"
```
