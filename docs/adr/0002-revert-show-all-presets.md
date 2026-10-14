# 0002. Reverted: show-all-presets dropdown with model-tagged auto-select

Status: Accepted

## Context

Commit `94bfb43` changed the preset dropdown to list every preset and, on model change, auto-select the first preset tagged for that model, falling back to the catch-all. It was made locally while pass 1 (ADR 0001) was built separately from `origin/main`, and the two redesigned the same code.

## Decision

`94bfb43` was reverted in `54ea382` before pass 1 landed (PR #1). The dropdown shows only presets that match the selected model, and model changes always reset the selection to Auto.

## Consequences

- With Auto as the default for every model, picking a tagged preset automatically would override `--fit` with hard-coded values, which is the thing ADR 0001 moved away from.
- Don't reintroduce auto-selection of tagged presets without revisiting ADR 0001.
