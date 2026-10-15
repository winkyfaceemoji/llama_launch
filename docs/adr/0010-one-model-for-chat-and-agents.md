# 0010. One model serves chat and agent jobs

Status: Accepted

## Context

The idea was a stronger model for chat and a lighter one for agent jobs, so agents would cost less GPU and RAM. On this PC (one 16 GB GPU, 32 GB RAM) that doesn't hold:

- Two models in VRAM at once take space from each other's weights and context, so both get weaker.
- An agent model on the CPU leaves chat alone, but agent steps read long files and pages, and CPU prompt processing makes each step take minutes.
- Swapping models with llama-server's router mode (`--models-dir`) costs 5–15 s per switch, and only one fits at a time.
- Agent work (10–40 tool calls, strict plan formats, long context) is harder for small models than chat. The 14B model already failed organise plans in the 2026-10-15 stress test until the validator and prompt were fixed.

The user ranks correct agent results first, uses chat for questions and planning, and sometimes chats while a job runs.

## Decision

One model, the strongest that fits, serves both chat and agent jobs. They take turns on the GPU through llama-server's parallel slots; chat gets no priority over a running job.

Rejected:
- Separate chat and agent models, swapped or loaded together (costs above).
- Chat-first priority, where the agent waits between steps while a chat reply is streaming. Its gain is partial (a step already running still shares the GPU), and a stuck "chat busy" flag could stall jobs.

## Consequences

- Picking a model is one question, answered by the model test (agent jobs, reasoning questions, speed) rather than two.
- Chat slows while a job runs.
- If model tests ever show one model clearly better at chat and another at tool use, router mode can split the roles with a config change. Write a new ADR that supersedes this one.
