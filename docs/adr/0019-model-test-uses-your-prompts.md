# 0019. The model test measures the user's own prompts; each result records them

Status: Accepted

## Context

The launcher's Prompts dialog (spec `docs/superpowers/specs/2026-10-21-prompt-editor.md`) lets the user edit every prompt: the chat system prompt and the four agent job prompts. Model tests already sent the user's chat prompt with the tool-choice cases, and the agent jobs would now use the edited job prompts too. Two questions followed: which prompts should a test measure, and how should the report keep runs with different prompts apart? `TEST_VERSION` alone couldn't do it, because a bump per edit hides every earlier result.

## Decision

A test runs with whatever prompts the user has saved, the ones used every day. `model_test.prompts_used` records all five texts and an 8-character SHA-256 `prompt_version` in each result. The report ranks only runs of the newest run's prompt version, lists other prompt versions below it saying which prompts differ, and leaves out results from before prompts were recorded (the files stay on disk). `TEST_VERSION` still bumps for the test's own cases, questions and scoring.

Rejected:
- **Always test with the shipped prompts:** rankings would stay comparable with every past result, but a test couldn't show whether an edit helped, and it would score a setup the user doesn't run. It also needs a test-only prompt path in both the proxy and the agents server.
- **A Shipped/Mine choice per run:** answers both questions, but doubles the GPU time (15 to 60 minutes per run) for model rankings on prompts the user doesn't use.
- **Keep unrecorded results as their own group:** they can't be compared with anything, so they would only add noise.

## Consequences

- After a meaningful prompt edit, earlier results stop counting in the rankings until the models are rerun.
- Prompts refined while using one model may favour it.
- `prompts_used` reads `config.json` when the run starts. That matches what the servers loaded only because the launcher allows prompt edits only while they're stopped.
