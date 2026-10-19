# 0013. Organise jobs propose their plan in batches

Status: Accepted

## Context

An organise job used to send its whole plan in one `propose_plan` call. In the model test of 2026-10-16, case O4 (a folder of about 200 files, a 205-operation plan) took 467 to 842 s for the Qwen models that finished it, and Ministral timed out after `llm.chat`'s 900 s limit with no plan. At 35 to 85 tokens/s, a reply holding every operation (plus thinking, on some models) sits close to the timeout, and a small `--fit` context (gemma-4-26B got 8704 tokens) has no room for it at all.

## Decision

`propose_plan` adds a batch to the plan. The prompt asks for at most `PLAN_BATCH` (25) operations per call; larger batches are still accepted. Each call expands and validates the whole plan so far, so a move into an earlier batch's `mkdir` works and overlaps across batches are caught. A rejected batch isn't added and the earlier batches stay, so the model can fix just that batch; if the job ends with its last batch rejected, the plan is dropped as before, so `result.md` can't describe a plan other than `plan.json`.

Rejected:
- A longer timeout for organise turns: a slow or thinking model still risks it, a stuck turn holds the GPU longer, and the reply still has to fit the context.
- A cap of N operations per plan, ending with a partial plan to approve and run again: the user would have to rerun the job until the folder is done.

## Consequences

- Big folders take several model turns instead of one long one; `MAX_CALLS` (40) leaves room for about 30 batches after looking around.
- Old batches' arguments are trimmed from the history like old tool results, so they don't fill a small context.
- The review call still sends the whole plan in one prompt.
