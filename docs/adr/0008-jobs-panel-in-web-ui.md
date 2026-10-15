# 0008. The proxy injects a jobs panel into the Web UI

Status: Accepted

## Context

Agent jobs were only visible in the launcher window's Jobs pane, away from the chat where they are started. Approving a plan or checking progress meant switching windows.

## Decision

- `proxy.py` adds one script tag to the Web UI page. The script draws a Jobs button at the top right and a 360 px iframe showing `/agent-jobs`, which the proxy serves from `panel/`.
- The panel's calls go through an allow-list on the proxy to the agents server's key-checked routes (`GET /jobs`, `POST /jobs/<id>/<action>`, `POST /trash/empty`). The proxy never adds a key; the panel reads the key the Web UI stores.
- One lock (`runner.FILE_MOVES`) covers apply, restore and Empty trash, so Empty trash can't delete files a move is using, whichever side started it.
- The launcher's Jobs pane, its approval dialog and its Empty trash are removed. Plans are still approved only where the whole plan is shown (ADR 0007), now in the panel.

Rejected:
- MCP UI resources: support in this Web UI is unverified.
- A separate page only: not beside the chat.
- Keeping the launcher pane: two places to act, both away from the chat.

## Consequences

- The proxy sends the page with `Cache-Control: no-store`, but a browser that cached the old page (llama-server marks it immutable for a year) needs one hard refresh (Ctrl+F5).
- Panel files must send COEP `require-corp`, because the Web UI page does and blocks anything embedded in it that doesn't.
- A Web UI redesign can at worst misplace the button; the chat and the agents server are unaffected.
