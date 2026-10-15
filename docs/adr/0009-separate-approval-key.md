# 0009. The jobs panel uses its own approval key

Status: Accepted

## Context

The agents server's panel routes (`/jobs`, `/jobs/<id>/apply|reject|restore|queue`, `/trash/empty`) checked the llama API key (ADR 0007). That key also goes to every client that chats with llama-server: the Web UI, local agents, scripts. Any of them could approve a plan that moves or removes files, although approving is meant to happen only in the panel, after the user has read the whole plan.

## Decision

- The launcher creates a second random key, `approval_key.txt` (gitignored), next to `api_key.txt`. The agents server's panel routes accept only this key.
- The panel keeps it in `localStorage["agentJobs.approvalKey"]`. The user pastes it into a field in the panel, which shows while the key is missing or refused. The launcher's running view has a copy link for it.

Rejected: the proxy adding the key to panel requests. Any page that can make the browser send a request to the proxy would then act with the key. Embedding the key in the panel page has the same problem for anything that can fetch that page, such as the fetch MCP tool.

## Consequences

- A second key to paste once, and again whenever `approval_key.txt` is replaced.
- Programs already running on the PC can still read the file; like ADR 0005, this keeps out key holders and web pages, not local malware.
- The eval harness reads `approval_key.txt` for the panel routes.
