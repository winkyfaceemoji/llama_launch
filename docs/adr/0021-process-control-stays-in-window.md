# 0021. Process control stays in the window; no server-stack module (for now)

Status: Accepted

## Context

The restructure (ADR 0020) considered a server-stack module to own the four processes: free the ports, launch in order, kill in the documented order, clean up jobs, clear the test lock, stream output, and report health, with no tkinter, testable with stand-in processes. Three interface designs were drawn up; the smallest put every ordering rule inside one `stop()`.

## Decision

Not done. Process control stays in the window class, untouched: `launch` (renamed from `run_commands`), `_start_agents_server`, `_free_port`, `_kill_proc`, `_kill_all`, the health poll and the model test. The only change is a real `running` flag in place of reading whether the Exit button is on screen.

Why, ranked by risk against value: it was the riskiest rewrite and the least valuable.
- **The risk.** It rewrites working Windows process code with four ordering rules (test before kills, the health poll before kills, job cleanup after the agents server dies, the lock last) and `taskkill /T` and netstat specifics. A slip shows up only in use: a llama-server holding VRAM, a cancelled test job left running, a live apply marked failed.
- **The value.** That code works and rarely changes, and real-subprocess tests on Windows are slow and flaky.

## Consequences

- Process control is still untested and still tangled with the GUI.
- **If it's revisited:** start from the "smallest interface" design. That's `ServerStack(post, on_event)` with `start(plan)`, `test()`, `stop(test_only=False)` and a `phase`, plus four event types (Line, Pid, Health, TestEnded) and a pure `plan()`. The model test lives inside the stack, so `stop()` holds every ordering rule. Write real-subprocess tests for the four ordering rules before removing the old code.
- Also worth folding in then: the update build check's separate llama-server launch on port 18081 (`UpdateRunner`).
