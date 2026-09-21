# Bot plugins

Plugins are deliberately loaded through a narrow Python contract:

```python
def register(registry):
    registry.register_tool("example_lookup", handler, read_only=True, risk="low")
```

Plugin handlers must call existing domain services and return bounded,
JSON-serializable values. They do not receive raw Discord tokens, Mongo
queries, or direct access to hidden-channel content. Any future mutating tool
must be listed in `AGENT_APPROVAL_TOOLS` and pass through the approval gate.
