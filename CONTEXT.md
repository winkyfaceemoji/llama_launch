# CONTEXT

Glossary for the launcher. One line per term.

- **Build**: one llama.cpp release, named by its number (`b9776`). A build includes llama-server and the Web UI, which is built into it.
- **Update**: replacing the current build with a newer one. The launcher offers it at startup; it never happens without a click.
- **Previous build**: the build that was current before the last update. Only its number is kept; WinGet installs builds, one at a time.
- **Roll back**: reinstalling the previous build through WinGet, from Settings, whenever a previous build is recorded.
- **Model**: the one loaded model that serves both chat and agent jobs; they take turns on the GPU.
- **Model test**: the run (15 to 60 minutes), started from the launcher, that scores the loaded model on agent jobs, fixed reasoning questions and speed, and adds a row to the test report. It uses each model's own sampling defaults, blocks Web UI chat while it runs, and its agent jobs are test jobs.
- **Test report**: the local HTML page the launcher writes and opens in the browser after each model test: one row per test, sorted by agent score, each expanding to per-case results and the model's wrong answers. It never leaves this PC.
- **Test job**: an agent job a model test created; it never shows in the Jobs panel, and the next model test replaces it.
- **Shipped prompt**: the default text of a prompt as committed in code; what Reset to default restores. Your edits live in `config.json`.
- **Prompt version**: a short fingerprint of the five prompts a model test ran with; the test report only ranks runs of the same prompt version.
- **Build check**: the test run right after an update (last model, spare port, key and foreign `Origin` refused); a failed check rolls back.
