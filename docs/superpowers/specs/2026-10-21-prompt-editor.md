# Prompt editor: design

Agreed in chat on 2026-10-21 (after an LLM council on the idea). Terms (shipped prompt, prompt version, model test, test report) are in [CONTEXT.md](../../../CONTEXT.md).

## Goal

See every prompt the models get, from the launcher, and change any of them without touching code or `config.json` by hand. Model tests measure the prompts in daily use. Chat answers that cite pages the model never opened get flagged, the way agent reports already are.

## Decisions

- **One Prompts dialog for all five prompts:** Chat, Research, Plan, Organise, Review. Full control: every prompt is editable.
- **Opened from Settings:** a "Prompts" row with a **Manage…** button, like Agent folders. It's a child of Settings and hands the grab back on close, as `FoldersDialog` does.
- **Read any time, edit only while the servers are stopped.** While running, the text is read-only, Save and Reset are disabled, and a line says "Stop the servers to edit." The proxy and agents server read prompts once at startup, so locking edits while they run means the dialog always shows what they're using. No live reload.
- **The model test uses your saved prompts.** No shipped-prompt test mode. Each result records its prompt version, and the report ranks only runs of the current prompt version.
- **Results without a recorded prompt version** (everything before this change) are left out of the report; the files stay on disk.
- **Chat citation check in the proxy:** a chat answer citing a URL the conversation never fetched gets a warning line, like agent reports.

## Prompts dialog

- Resizable, built like `PresetDialog`. A row of five tabs or buttons picks the prompt; below it, one large scrolling `Text`.
- For each prompt:
  - one line saying what it feeds (e.g. "Research jobs. Starts with today's date, added in code.");
  - a **modified** marker when the text differs from the shipped prompt;
  - a character count (prompt length comes out of each agent job's context budget);
  - **Reset to default**, which restores the shipped prompt.
- Chat shows a read-only preview line of what the proxy adds in front: `Current date: <today>`.
- **Save checks placeholders** and refuses with a message naming the problem:
  - Organise must use exactly `{folder}` and `{batch}`; Review exactly `{goal}`. Literal braces in those two must be doubled (`{{ }}`). The check is `string.Formatter().parse` plus a trial `format()` with sample values.
  - Chat, Research and Plan aren't filled in with `format`, so any text is fine there.
- Saving writes through `self.config` and `save_config`, so the launcher's in-memory copy never overwrites it. Before writing, it copies `config.json` to `config.json.bak` (one backup, replaced each save).

## Where prompts live

- **Shipped prompts** move from `agents/runner.py` into a new `agents/prompts.py` (standard library only): a dict of the four agent defaults, the allowed placeholders per prompt, `check(name, text)`, and `effective(config)`, which returns each agent prompt from `config["agent_prompts"]` if set and non-empty, else the default. The shipped chat prompt stays `_DEFAULT_CONFIG["system_prompt"]`.
- **Your edits:** chat in `config["system_prompt"]` (as today), agents in `config["agent_prompts"]` (`{"research": ..., "plan": ..., "organize": ..., "review": ...}`, keyed by job kind; missing or empty means the shipped prompt). Reset deletes the key, so a later improvement to a shipped prompt reaches you unless you've edited that one.
- **The agents server** reads `config.json` once at startup (server code; agents' tools still can't read the launcher folder) and passes the result of `prompts.load` to the runner. A missing or unreadable `config.json` means shipped prompts; an edit that fails the check (a hand edit) falls back to the shipped prompt with a log line.

## Model test: prompt version

- `model_test.py` records `prompts`: the full text of all five prompts in use (chat from `config.json`, agents from `effective(config)`), and `prompt_version`: the first 8 hex characters of a SHA-256 over them in a fixed order.
- The report ranks only runs of the current test version **and** the current prompt version (the newest run's). Runs of other prompt versions get their own section below, one group per version, each naming which prompts differ from the current ones. Runs without `prompt_version` aren't shown.
- `TEST_VERSION` no longer needs a bump when prompts change; the prompt version covers that. It still bumps for cases, questions and scoring.
- Reasoning questions keep going straight to llama-server with no system prompt, so prompt edits don't move the reasoning score.

## Chat citation check (proxy)

- **Fetched URLs:** from the request's `messages`, the `url` argument of every assistant tool call to the fetch tool, in the whole conversation the Web UI sends. URLs that appear in the user's own messages also count as known.
- **Cited URLs:** `runner.cited_urls` on the answer text (same rules as agents: URLs in code don't count, a link alone in backticks does).
- **Only the final answer is checked** (a reply with no tool calls). If it cites URLs that weren't fetched, the proxy adds one line at the end:
  `> Warning: cites pages that were never opened: <urls>`
  - **Streaming:** bytes still go to the browser as they arrive. The proxy keeps a copy of the content deltas and sends one extra content chunk just before `data: [DONE]`.
  - **Non-streaming:** the warning is appended to the message content.
- Nothing is sent back to the model; the Web UI drives the tool loop, not the proxy.

## Before building: verify

- How the Web UI sends fetch tool calls through `:8080`: the tool's name as the model sees it, its arguments, and whether earlier turns' tool calls stay in `messages`. Capture one real request with a fetch, then design the parser against it. If tool calls never pass through the proxy, the citation check is dropped and this spec is amended.

## Build order

1. `agents/prompts.py`; the runner and agents server use it; tests for `check` and `effective`.
2. `save_config` backup; Prompts dialog in Settings; `dev/gui_shot.py` shows it.
3. Prompt version in `model_test.py` and the report.
4. Verify the Web UI's tool-call format, then the proxy citation check with tests.

## Docs to update with the change

- `.claude/rules/agents.md` (prompts come from `config.json` via `prompts.py`), `launcher.md` (Prompts dialog, edit only while stopped), `proxy.md` ("read once at startup" stays; the citation check), `eval.md` (prompt version instead of a `TEST_VERSION` bump for prompts).
- ADR: the model test measures the user's prompts; a shipped-prompt test mode was rejected.
- README: the Prompts dialog.

## Out of scope

- Live reload of prompts while the servers run.
- A shipped-prompt test mode, prompt sets, shared prompt fragments, per-job prompt hashes.
- Changing what the shipped prompts say (your rule 6 rewrite goes in your copy, through the dialog).
