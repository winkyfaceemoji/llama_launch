# 0003. --context-shift and --swa-full stay off

Status: Accepted

## Context

The Gemma preset passed `--context-shift` so long chats would keep going, plus `--swa-full`, which context shift needs on sliding-window models. `--swa-full` makes every layer cache the full context instead of a small window, so at 32k–131k context the KV cache grows several times over and can push a 12.7 GB model out of 16 GB VRAM. Context shift also drops the oldest messages without telling the user.

## Decision

Neither flag is in the Auto preset or the docs' recommended setup. When a chat fills the context, start a new chat.

## Consequences

- Sliding-window models (Gemma, gpt-oss) keep their small KV cache, which leaves `--fit` room for a much larger context.
- A full chat stops with an error instead of quietly forgetting its start.
