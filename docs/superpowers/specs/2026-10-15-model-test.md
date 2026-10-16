# Model test: design

Agreed in grill sessions on 2026-10-15. Terms (model, model test, test report, test job) are in [CONTEXT.md](../../../CONTEXT.md); ADR 0010 is why one model serves chat and agents.

## Goal

Answer "which model works best on this PC for agent jobs and chat?" with one button. A run scores the loaded model on agent jobs, reasoning questions and speed. Every complete run adds a row to a local HTML report that compares all models tested so far, per skill.

## Decisions

- **Start:** a **Test this model** button in the running view, next to Exit Processes, enabled once llama-server reports healthy. A confirm dialog says it takes about an hour, uses the GPU and blocks Web UI chat. It tests whatever model is loaded.
- **Always the full run** (no quick mode).
- **Built on the agents stress test:** the agent part is `eval/stress_agents.py`'s cases as they are now. `python eval/stress_agents.py` keeps working on its own (`--cases`, `--no-tools`).
- **Sampling:** each model's own defaults. The test sends no temperature or other sampling settings, so scores move a little between runs, and a gap of a few points means little.
- **Chat is blocked while a test runs.** The prompt proxy (8080) answers Web UI chat requests with a clear "a model test is running" error. The test talks to llama-server directly on 8081 and isn't affected. A launcher start and Exit Processes clear a block left over from a crash.
- **Stop:** the dialog has a Stop button. A stopped test leaves nothing in the report (complete runs only). Its plans are rejected and its temporary folders deleted.
- **Test jobs** (jobs a test creates) never show in the Jobs panel. The next test deletes the previous test's jobs, trash included.

## What a run measures

### Agents: equal weight per area

| Area | Cases |
|---|---|
| Organise | O1–O5 |
| Risky-plan review | A1–A5 |
| Research | R1–R3 |
| Plan | P1 |
| Tool choice | each tool-choice prompt |

- Each case passes or fails against a fixed automatic rule, set before any run. No manual checks: "R3 must not invent release notes" becomes "every cited page was opened by the job", which the research fix already enforces.
- **Agents score = the mean of the area percentages**, so every skill counts the same whatever its case count. Plan has one case, so it swings between 0 and 100 and moves the score by 20 points either way.
- Safety (operations outside the job's folder, changes without approval) is not an area. Any violation marks the row **unsafe** in red, whatever the score.

### Reasoning: 40 fixed questions, equal weight per kind

- Ten questions each of maths word problems, logic puzzles, instruction following (exact format, length or order) and dates and times.
- Every question has one checkable answer (exact or pattern match; no AI judge). They're written fresh for this repo, so no model has seen them.
- Reasoning score = the mean of the four kind percentages.

### Speed

- Generation and prompt tokens per second, from llama-server's own timings on the reasoning questions.
- The context `--fit` picked, from `/props`.

## Test report

- `eval/results/report.html`, regenerated after every complete run from all result files in `eval/results/` (gitignored), and opened in the browser. It never leaves this PC.
- One row per test: model, build, date, agents %, reasoning %, generation tokens per second, context. Rows are sorted by agents %.
- A row expands to:
  - per-area and per-kind percentages;
  - every case with pass or fail;
  - the model's wrong answers;
  - any safety violation.

## Out of scope

- A quick mode, downloading models, ranking models found online.
- Comparing chat quality beyond the reasoning questions.

## Open points for the plan

- **Stopping running jobs.** The agents server has no per-job cancel, so a job already running when you press Stop would keep running. The plan must add a cancel for test jobs, checked between model turns, or make Stop wait for them.
- **How the proxy learns a test is running**, for example a lock file it checks per request, and which Web UI requests count as chat.
- **Marking test jobs** so the panel never lists them, including in the moment between a job's creation and its marking.
- **The exact pass rule per case**, written into the harness before the first run.
- Where the dialog's log comes from (the harness's output streamed into the dialog).
