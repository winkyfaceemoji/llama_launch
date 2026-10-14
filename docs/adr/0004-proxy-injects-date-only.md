# 0004. Proxy injects the date only

Status: Accepted

## Context

The proxy put the date and time, to the minute, at the top of the system prompt. llama-server reuses its KV cache only for the part of the prompt that hasn't changed, so each new minute invalidated the whole conversation and long chats paid tens of seconds of reprocessing before the first token. The exact time came from an MCP time server configured for the wrong timezone.

## Decision

The proxy injects only the date (`%A, %B %d, %Y`). The exact time is available on demand through llama-server's built-in `--tools get_datetime`, which reads the PC clock. Moving the timestamp to the end of the messages was the rejected alternative: it still breaks the cache for the latest turn, and the model rarely needs the minute.

## Consequences

- The prompt prefix changes once a day, so prompt caching works across turns.
- The MCP time server is no longer needed.
