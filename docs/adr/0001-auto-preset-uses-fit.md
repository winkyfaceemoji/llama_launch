# 0001. Auto preset uses llama-server's --fit

Status: Accepted

## Context

Presets used to hard-code `-c` and `-ngl 999` per model family (Gemma at 131072 context, Qwen at 32768, an 8192 catch-all). On a 16 GB RX 7800 XT the right context depends on the model, its quant and how much VRAM is free at launch. A context that is too big doesn't fail: the driver spills to system RAM and generation slows several times over. The catch-all was also listed first, so the tuned presets were rarely picked.

## Decision

One built-in "Auto (fit to VRAM)" preset leaves `-c` and `-ngl` unset, so llama-server's `--fit` (on by default in 2026 builds) picks the largest context that fits in free VRAM with a 1 GB margin. Auto always exists, is selected for every model, and can't be deleted. Per-model tuned presets were rejected as the default; users can still add them as manual overrides.

## Consequences

- Context size is chosen at launch and shown in the llama-server log (Gemma 4 12B Q8_0 got 131072 on this card, using about 13.2 GB of VRAM).
- Adding `-c` or `-ngl` to a preset turns `--fit` off for that value.
- Needs a llama.cpp build with `--fit`.
