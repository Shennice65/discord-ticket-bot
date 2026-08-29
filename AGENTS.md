# Codex Project Guidelines

Apply these rules to coding, review, debugging, and refactoring work in this repository. Use the installed `karpathy-guidelines` skill when a task benefits from the full workflow.

## Think Before Coding

- Inspect the relevant code and state material assumptions before implementing.
- Surface genuine ambiguity, conflicting requirements, and meaningful tradeoffs.
- Ask for clarification only when repository context cannot resolve a decision safely.

## Keep It Simple

- Implement the smallest complete solution to the requested problem.
- Do not add speculative features, one-use abstractions, or unnecessary configurability.
- Prefer existing project patterns and dependencies over new machinery.

## Make Surgical Changes

- Every changed line must trace to the request or to cleanup made necessary by the change.
- Preserve unrelated code, comments, formatting, and user work.
- Match the style of the code being edited. Mention unrelated issues instead of fixing them silently.

## Verify the Outcome

- Define observable success criteria for non-trivial work.
- Reproduce bugs when practical, then verify the fix with the narrowest relevant checks.
- Run proportionate tests, linting, builds, or syntax checks before reporting completion.
- Do not claim success when required verification failed or was skipped; state the limitation clearly.
