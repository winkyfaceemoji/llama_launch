# GGUF models for chat + tools + agent jobs on a 16 GB GPU (October 2026)

Research date: 2026-10-15. Target setup: RX 7800 XT 16 GB, 32 GB RAM, Windows 11, llama.cpp b11636
(commit `529ac893e`, published 2026-10-15 [S30]), Vulkan, Auto preset (`--fit`, `--flash-attn on`, q8_0 K and V cache, no `-c`/`-ngl`).
Workload: one model for Web UI chat with MCP tools (fetch, search, time) and for agent jobs (JSON move/rename plans, cited research reports, plan review).
Coding agents are covered separately in [local-coding-agents-2026.md](local-coding-agents-2026.md); its SWE-bench / Terminal-Bench numbers are not repeated here.
All numbers are as published by the source named. "Vendor" means the model's own publisher ran it.

## Bottom line

- **Best fit on paper: Qwen3.6-35B-A3B at a 3-bit Unsloth quant (UD-IQ3_S, 13.7 GB) [S10].** It is MoE with 3B active, so it runs from VRAM at MoE speed. It needs only about 10.6 KiB of KV per token, so `--fit` can give it long context. Qwen-run agent scores are close to Qwen3.5-27B: TAU3 67.2, MCP-Atlas 62.8, MCPMark 37.0 [S2]. The predecessor Qwen3.5-35B-A3B scores BFCL-V4 67.3 and TAU2 81.2 [S3]. The cost is a 3-bit quant (mean KLD about 0.05 on the 3.5 version [S20]) and an **open** llama.cpp bug where about a third of Qwen3.6 tool calls on one prompt land in `content` instead of `tool_calls` [G1].
- **Quality pick: Qwen3.8-27B (dense, Aug 2026).** It has the highest IFBench in any table found (79.5, Qwen-run [S4]). At UD-IQ4_XS (14.3 GB) it leaves about 27K context under `--fit`. As a dense 27B on this card, expect around 30 t/s at best (bandwidth estimate, not measured).
- **Newest contender: Meta Muse Glimmer 30B (dense, Apache-2.0, Aug 2026).** Its agentic numbers are the strongest found: MCP Atlas 75.5 against 62.5 for Qwen3.6-27B, and DeepSearch QA 74.6 (Meta-run) [S5]. llama.cpp has a dedicated parser and architecture for it [S31][S32], and its KV cost is only 6.9 KiB/token. **However, reasoning cannot be turned off** [S6], every chat turn pays for thinking, and only 3-bit quants fit in 16 GB.
- **Gemma 4 26B-A4B has an official Google QAT Q4_0 GGUF of 14.4 GB that fits in VRAM** [S8], with TAU2 68.2 (Google-run [S7]). It is a risk under this launcher's fixed q8_0 KV cache: an independent test measured KL 0.377 for Gemma 4 26B-A4B with q8_0 KV, against 0.024–0.039 for Qwen3.6 [S21]. Two Gemma 4 tool-call parser bugs are also open [G4].
- **Your current Qwen3-14B Q5_K_M is outclassed for this job.** Its native context is 40,960 and it costs 85 KiB of KV per token. Qwen3.5-9B (Feb 2026) scores BFCL-V4 66.1 and TAU2 79.1 (Qwen-run) [S3a], about the same as 3.5-35B-A3B, and runs its full 262K context at Q8_0 (9.53 GB) in 16 GB.
- **`--fit` shrinks context before it offloads anything** (step 2 in `common/fit.cpp`, down to `--fit-ctx`, default 4096). Only if the model still doesn't fit does it push layers, or for MoE the sparse expert tensors, to RAM [S33]. A file of 15–16 GB therefore gets a tiny context rather than expert offload. Plan on weights of 14.5 GB or less.

## 1. Candidates that fit or nearly fit 16 GB

Budget: 16 GiB VRAM (17.18 GB) minus the 1024 MiB `--fit` margin is about 16.1 GB, before compute buffers and desktop use. Sizes are decimal GB from the HF file listing.

| Model (release, licence) | Type | GGUF quants that fit (GB) | Native ctx | llama.cpp b11636 | Source |
|---|---|---|---|---|---|
| Qwen3.6-35B-A3B (Apr 2026, Apache-2.0) | MoE 35B/3B active, hybrid Gated DeltaNet + attention | UD-IQ3_XXS 13.2, UD-IQ3_S 13.7, UD-Q3_K_S 15.4 (too big for long ctx); UD-IQ4_XS 17.7 needs offload [S10] | 262,144 | arch `qwen35moe` [S32]; parser: Qwen3-Coder XML [S31] | [S2] |
| Qwen3.5-35B-A3B (Feb 2026, Apache-2.0) | same layout | same sizes class (Unsloth KLD table: IQ3_XXS 13.12, IQ3_S 14.13 [S20]) | 262,144 | as above | [S3] |
| Qwen3.8-27B (Aug 2026, Apache-2.0) | dense, hybrid DeltaNet | UD-Q3_K_XL 13.1, UD-IQ4_XS 14.3 [S11] | 262,144 | arch `qwen35`; b11636's own release note benchmarks Qwen3.8-27B IQ3_S [S30] | [S4] |
| Qwen3.6-27B (Apr 2026) | dense, hybrid | Q3_K_M 13.6, IQ4_XS 15.4 (see coding note) | 262,144 | yes | coding note |
| Qwen3.5-9B (Feb 2026, Apache-2.0) | dense, hybrid | Q8_0 9.53, Q6_K 7.46, Q4_K_M 5.68 [S12] | 262,144 | yes | [S3a] |
| Muse Glimmer 30B (Aug 2026, Apache-2.0, Meta) | dense 29.6B, 3 local : 1 global attention, SWA 2048 | UD-IQ3_XXS 13.1, UD-Q3_K_XL 13.4, UD-IQ3_M 14.1, UD-Q4_K_XL 15.9 (unsloth) [S13]; Meta's own "17GB" Q4_K_M is 16.8 [S6] | 131,072 | arch `muse-glimmer` (PR #26841, needs b10353+ [S6]); dedicated parser [S31] | [S5] |
| Gemma 4 26B-A4B (Apr 2026 per Wikipedia; card states no date; Apache-2.0) | MoE 25.2B/3.8B active, 5 global + 25 SWA-1024 layers | Google QAT Q4_0 14.4 [S8]; unsloth UD-Q3_K_XL 12.9, UD-IQ4_XS 13.6 [S14] | 262,144 | arch `gemma4`; dedicated parser [S31][S32] | [S7] |
| Gemma 4 12B "Unified" (mid-2026, Apache-2.0) | dense 11.95B, 8 global + 40 SWA layers | Google QAT Q4_0 6.98 [S9]; you have Q8_0 (12.7) | 262,144 | yes | [S7] |
| Gemma 4 31B (Apr 2026) | dense 30.7B, 10 global + 50 SWA | Q3_K_S 13.2, Q3_K_M 14.7 [S15]; QAT Q4_0 repo exists, size not checked | 262,144 | yes | [S7] |
| gpt-oss-20b (Aug 2025, Apache-2.0) | MoE (see coding note) | MXFP4 12.1 [S16] | 131,072 (**Unverified**, not re-checked) | arch `gpt-oss`; dedicated Harmony parser [S31] | coding note |
| Ministral 3 14B Instruct 2512 (Dec 2025 by the "2512" suffix; paper Jan 2026; Apache-2.0) | dense 14B (+vision) | Q4_K_M 8.24, Q5_K_M 9.62, Q8_0 14.4 (official) [S17] | 256K | arch `mistral3`; dedicated Ministral 3 parser [S31] | [S17] |
| Granite 4.2 30B / 8B (2026-08-25, Apache-2.0, IBM) | dense, 64 layers x 8 KV x 128 (30B) | GGUF sizes not checked | 131,072 | arch `granite`; no dedicated parser (autoparser) [S31] | [S18] |
| Qwen3-14B (current) | dense | Q5_K_M 10.5 (yours) | 40,960 | yes | coding note |

Checked and excluded:
- **Nemotron 3.5 Lightning 30B-A3B** (NVIDIA, 2026-08-11, OpenMDW-1.1). The smallest ggml-org GGUF is Q4_0 at 18.9 GB [S19b]. NVIDIA's own table puts it below Qwen3.6-35B-A3B on every agentic row, e.g. τ³-Banking 9.28 vs 10.52 [S19].
- **Kolibri-1** (Aleph Alpha, 2026-10-03). Secondary coverage gives 78B total / 3.46B active MoE. That is too big for 16 GB + 32 GB at a usable quant. **Unverified**: the model card was not read.
- **Mistral Small 4** is 119B MoE (coding note). The newest open Mistral dense small model found is Ministral 3 14B.
- **No newer Phi** than the Phi-4 family was found, and **no gpt-oss successor** was found.
- **GLM-4.7-Flash** (30B-A3B, Jan 2026, MIT) was seen only in secondary sources. Its card, KV layout and GGUF sizes were not checked. **Unverified.**
- **Qwen3.8 small or MoE sizes:** none found. The Qwen3.8 MoEs are 2.4T-A95B and Flash-Next (about 180B) [S1].

## 2. Tool calling and instruction following

### Published scores (all vendor-run unless marked)

| Model | BFCL-V4 | TAU2 / TAU3 | MCP / search agents | IFBench | Long ctx | Source |
|---|---|---|---|---|---|---|
| Qwen3.6-35B-A3B | not reported | TAU3 67.2 | MCP-Atlas 62.8, MCPMark 37.0, WideSearch 60.1 | n/r | n/r | [S2] |
| Qwen3.5-35B-A3B | 67.3 | TAU2 81.2 | BrowseComp 61.0, WideSearch 57.1 | 70.2 | AA-LCR 58.5, LongBench v2 59.0 | [S3] |
| Qwen3.5-27B | 68.5 | TAU2 79.0; TAU3 68.4 [S2] | MCP-Atlas 68.4 [S2] | 76.5 | AA-LCR 66.1 | [S3] |
| Qwen3.5-9B / 4B | 66.1 / 50.3 | TAU2 79.1 / 79.9 | n/r | 64.5 / 59.2 | AA-LCR 63.0 / 57.0 | [S3a] |
| gpt-oss-20b (Qwen's table) | -- | -- | n/r | 65.1 | AA-LCR 30.7 | [S3a] |
| Qwen3.8-27B | not reported | not reported | CoWorkBench 70.7 | **79.5** | n/r | [S4] |
| Muse Glimmer 30B (high reasoning) | not reported | τ3-Banking 23.5 (Qwen3.6-27B 16.7, Gemma4-31B 15.1, same table) | MCP Atlas 75.5 (Qwen3.6-27B 62.5), DeepSearch QA 74.6 | 77.0 (in Qwen's table [S4]) | n/r | [S5] |
| Gemma 4 26B-A4B / 12B / 31B | not reported | TAU2 68.2 / 69.0 / 76.9 | n/r | n/r | MRCR v2 128K 44.1 / 43.4 / 66.4 | [S7] |
| Gemma 4 26B-A4B (Qwen-run) | -- | TAU3 59.0 | MCP-Atlas 50.0, MCPMark 14.2 | -- | -- | [S2] |
| Granite 4.2 30B / 8B | 61.39 / 52.39 | τ³ avg 62.00 / 58.06 | n/r | 77.17 / 79.33 | RULER 128K 81.38 / 71.41 | [S18] |
| Qwen3-14B (current) | BFCL **v3** 70.4 (thinking) | n/r | n/r | n/r | n/r | coding note |

- **No independent tool-calling numbers exist for any of these models.**
  - The BFCL page was last updated 2026-04-12 and its rows did not render for the fetcher [S22].
  - taubench.com shows only top-3 entries, all frontier or 397B-class [S23].
  - NVIDIA's Nemotron card is the only cross-vendor table run under one harness, and it covers τ³-Banking, not TAU2 [S19].
- Vendor tables disagree on the same model. Gemma 4 26B-A4B gets TAU2 68.2 from Google and TAU3 59.0 from Qwen (a different benchmark version).
- Muse Glimmer's numbers come from Meta's own table, and Qwen's table reuses some of them. Nobody else has measured it.

### llama.cpp support at b11636 (source read at `529ac893e`)

`common/chat.cpp` picks a dedicated parser by searching the template text, and falls back to an "autoparser" otherwise [S31]. The function-calling doc's family list is out of date (it still lists Qwen 2.5 and Hermes) [S34], so the source is the reference here.

| Family | Parser at b11636 | Open problems found |
|---|---|---|
| Qwen3.5 / 3.6 / 3.8 (also Nemotron 3.x, Granite 4.2 use the same XML format [S18][S19]) | "Qwen3-Coder" XML parser (`<tool_call><function=…><parameter=…>`) | [G1] #30118, **open**, filed 2026-10-07 against b11382: Qwen3.6-35B-A3B sometimes omits `<tool_call>`, and the call leaks into `content` (9 leaked of 24 on the reporter's test, using a community template). No fix merged. The token-aware parser PR #29307 is still a draft [S35]. [G2] #27767: `tool_choice: required` / named is not enforced when `enable_thinking=false`. |
| Gemma 4 | dedicated `gemma4` parser. GGUFs with an old template trigger a warning and a "legacy" compatibility path [S31] | [G4] #29655, **open** (2026-09-29, Vulkan, Gemma4 26B MoE): unstable multi-line tool arguments while streaming. The reporter later could not reproduce it on master. |
| Muse Glimmer | dedicated parser (`<atem:function_calls>`, `<|eom|>`) | [G5] #27025: about 1 in 5 tool calls returned HTTP 500. A fix (#29242) is in v0.5.0 = b11146 [S36], which is earlier than b11636. The card warns that stopping on `<|eom|>` breaks parallel tool calls [S6]. |
| gpt-oss | dedicated Harmony parser | The maintainers' guide says tool calls fail when the client doesn't send back the previous reasoning, and calls this an open problem across clients [S24]. |
| Ministral 3 | dedicated parser (Mistral Small 3.2 is excluded and goes to the autoparser) | none checked |
| Granite 4.2, Qwen3 (Hermes JSON) | detection above or autoparser | none checked |

Thinking controls:
- **Qwen3.6 / 3.8:** `chat_template_kwargs: {"enable_thinking": false}`. Qwen3.8 also takes `reasoning_effort` (default xhigh). `preserve_thinking` keeps earlier reasoning [S2][S4].
- **Gemma 4:** thinking is on when the system prompt starts with `<|think|>`. Thinking must be kept in history for tool-call turns [S7].
- **Muse Glimmer:** reasoning cannot be disabled (`--reasoning` and `reasoning_effort: none` have no effect). You can only lower `reasoning_strength` or set `--reasoning-budget N` [S6].
- **Granite 4.2:** `enable_thinking`, plus a `low_effort` option [S18].

## 3. KV cache and expected context under `--fit`

Formula as in the coding note: 2 × global-attention layers × KV heads × head_dim × 1.0625 bytes (q8_0). Dimensions come from each `config.json`. DeltaNet / Mamba layers keep a fixed state.
- Sliding-window layers add a fixed buffer of about (window × parallel slots + ubatch) cells, not a per-context cost.
- Gemma 4 sets `attention_k_eq_v`, but llama.cpp still stores K and V separately (`src/models/gemma4.cpp` passes a separate `Vcur`) [S37], so both are counted.

| Model / quant | Weights GiB | KV KiB/token (global) | SWA fixed buffer | Expected ctx under `--fit` (estimate) |
|---|---|---|---|---|
| Qwen3.6-35B-A3B UD-IQ3_S | 12.76 | 10.6 (10 layers x 2 x 256) | none (DeltaNet state) | about 130K |
| Qwen3.8-27B UD-IQ4_XS / UD-Q3_K_XL | 13.32 / 12.20 | 34 (16 x 4 x 256) | none | about 27K / 60K |
| Muse Glimmer 30B UD-Q3_K_XL | 12.48 | 6.9 (13 x 2 x 128) | 20.7 KiB x ~2.5K cells ≈ 0.05 GiB | full 131,072 likely |
| Gemma 4 26B-A4B QAT Q4_0 | 13.41 | 10.6 (5 x 2 x 512) | 106 KiB x ~1.5K cells ≈ 0.16 GiB | about 60K |
| Gemma 4 12B Q8_0 (yours) | 11.83 | 8.5 (8 x 1 x 512) | 170 KiB x ~1.5K ≈ 0.25 GiB | about 250K |
| Gemma 4 31B Q3_K_S | 12.29 | 42.5 (10 x 4 x 512) | 425 KiB x ~1.5K ≈ 0.62 GiB | about 30K |
| Qwen3.5-9B Q8_0 | 8.88 | 17 | none | full 262,144 |
| gpt-oss-20b MXFP4 | 11.27 | 12.75 | small | full 131,072 |
| Granite 4.2 30B | not checked | 136 (64 x 8 x 128) | none | poor: 32K = 4.25 GiB |
| Qwen3-14B Q5_K_M (yours) | 9.78 | 85 | none | capped at native 40,960 |

- **The context column is an estimate, not a measurement.** It assumes about 15.0 GiB usable, minus about 0.8 GiB for compute buffers and desktop (that 0.8 GiB is an assumption), with the rest going to KV. Check `n_ctx_slot` in the llama-server log.
- The SWA buffer scales with the number of parallel slots. The 1.5K–2.5K figures assume one stream; with more slots, multiply.
- Research jobs that feed in several fetched pages want 32K or more. Qwen3.8-27B at IQ4_XS is the one shortlisted model that falls short of that.

## 4. Speed on 16 GB AMD Vulkan

**No primary measurement exists for any shortlisted model on an RX 7800 XT.**

The anchors below come from three kinds of source: the llama.cpp scoreboard, community llama-bench posts and one user repo.

| Data point | Who / where | Numbers |
|---|---|---|
| RX 7800 XT, Llama-2 7B Q4_0 (3.56 GiB), Vulkan, FA on | llama.cpp Vulkan scoreboard, commit `4fdbc1e` [S25] | pp512 2197, tg128 124.9 |
| RX 7900 XTX, Qwen3.5-35B-A3B Q4_0 (19.78 GiB) fully on GPU, Vulkan | community llama-bench, HF discussion, Feb 2026 [S26] | pp512 2105–2209, tg128 94–105 |
| RX 7900 XTX, 7B Q4_0, Vulkan vs ROCm | llama.cpp issue #20934 [G6] | Vulkan about 167–177 t/s tg, ROCm 110–114 |
| RX 9060 XT 16 GB + 32 GB DDR4, Qwen3.6-35B-A3B UD-Q4_K_XL, `--n-cpu-moe 20`, MTP draft, 40K ctx | user repo (tcctr/LocalLLM) [S27] | tg 66 t/s (with MTP; no non-MTP figure) |
| RX 7900 XT, Muse Glimmer Q4-class + DFlash draft | discussion #26881 [S28] | "50–100 t/s" (user-reported) |

Rough expectations. **These are estimates, not measurements.**
- **MoE in VRAM (Qwen3.6-35B-A3B IQ3_S, Gemma 4 26B-A4B):** scaling the 7900 XTX result by the cards' 7B ratio (124.9 / ~172) gives about 65–75 t/s generation.
- **Dense, by weight size against the 7B anchor:**
  - Qwen3.8-27B IQ4_XS: about 33 t/s
  - Muse Glimmer Q3_K_XL: about 36 t/s
  - Gemma 4 12B Q8_0: about 38 t/s
  - Qwen3.5-9B Q8_0: about 50 t/s

  These are ceilings. Hybrid DeltaNet and SWA kernels are not covered by the anchor.
- **MoE with experts in RAM:** the only AMD data point is the 9060 XT one above, and it used MTP. The launcher passes no `--n-cpu-moe`, and `--fit` cuts context first (section 1).

Vulkan details at this build:
- v0.5.0 (b11146) added Vulkan IQ4_XS and IQ3_S matmul kernels [S36].
- A Muse Glimmer UD-Q4_K_XL file failed to load on one RX 7900 XT Vulkan setup, while UD-Q3_K_XL worked [S28]. That is a single user report with no root cause.

## 5. Quantisation floor

- **Qwen3.5-35B-A3B, Unsloth's KLD table** (Wiki-test, 512 ctx; mean KLD / 99.9% KLD) [S20]:
  - IQ2_XXS 0.185 / 4.22
  - Q2_K_XL 0.097 / 2.91
  - IQ3_XXS 0.050 / 1.53
  - IQ3_S 0.046 / 1.42
  - Q3_K_M 0.032 / 0.97
  - Q4_K_M 0.019 / 0.55
  - Q5_K_XL 0.007 / 0.24

  Unsloth says "2 bits cause more degradation" and warns that PPL/KLD can mislead. **The sensible floor for a 35B MoE is 3-bit.** No table was found for Qwen3.6, which has the same architecture.
- **Dense 27B–31B at 3-bit:** no publisher measured KLD for Qwen3.8-27B, Muse Glimmer or Gemma 4 31B. **Unknown.** Meta reports about 1.0% average benchmark loss for its 16.8 GB Q4_K_M and 0.2% for its 19.7 GB build (vendor-run) [S6]. Neither fits in 16 GB.
- **Gemma 4 QAT Q4_0:** Google claims "similar quality to bfloat16" but publishes no numbers for the GGUF [S9]. QAT Q4_0 is still the best-founded 4-bit option for Gemma.
- **KV cache, which this launcher fixes at q8_0:** an independent test (oobabooga, 2026-04-24, BF16 weights, f16 KV as reference) [S21] measured KL of:
  - Qwen3.6-27B 0.024
  - Qwen3.6-35B-A3B 0.039
  - Gemma 4 31B 0.108
  - Gemma 4 26B-A4B 0.377

  The units and averaging are not stated. The author concludes that "q8_0 is practically lossless" is wrong for Gemma. Your current Gemma 4 12B was not tested.

## 6. Shortlist (ranked for this workload)

| Rank | Model | Quant | File size | Expected ctx under `--fit` | Why |
|---|---|---|---|---|---|
| 1 | Qwen3.6-35B-A3B | UD-IQ3_S | 13.7 GB | about 130K (est.) | MoE speed from VRAM, long context, strong vendor TAU/MCP scores, low q8_0-KV sensitivity. Watch the tool-call leak bug [G1]. |
| 2 | Qwen3.8-27B | UD-IQ4_XS (or UD-Q3_K_XL 13.1 GB for about 60K ctx) | 14.3 GB | about 27K (est.) | Newest Qwen, best IFBench (79.5), strongest agent rows in Qwen's table. Dense, so slower; same parser family as rank 1. |
| 3 | Muse Glimmer 30B | UD-Q3_K_XL | 13.4 GB | 131,072 (est.) | Best vendor MCP/search-agent numbers, tiny KV, dedicated parser. Always reasons; the 3-bit quality loss is unknown. |
| 4 | Gemma 4 26B-A4B | Google QAT Q4_0 | 14.4 GB | about 60K (est.) | Fits in VRAM at QAT 4-bit, MoE speed. Most sensitive to q8_0 KV [S21]; open streaming tool bug [G4]. |
| 5 | Qwen3.5-9B | Q8_0 | 9.53 GB | 262,144 | Fast and safe fallback. BFCL-V4 66.1 / TAU2 79.1, about the same as 35B-A3B on Qwen's tables. |
| 6 | Gemma 4 12B (have it) | Q8_0 | 12.7 GB | about 250K (est.) | TAU2 69.0. Check the log for the "outdated gemma4 chat template" warning [S31]. |
| 7 | gpt-oss-20b | MXFP4 | 12.1 GB | 131,072 | Dedicated parser, but reasoning-history issues with clients [S24]; weak long-context score (AA-LCR 30.7 [S3a]). |
| — | Qwen3-14B (current) | Q5_K_M | 10.5 GB | 40,960 cap | Superseded: short native context and 85 KiB/token. |

### Try first

1. **Qwen3.6-35B-A3B UD-IQ3_S.** Repo `unsloth/Qwen3.6-35B-A3B-GGUF`, file `Qwen3.6-35B-A3B-UD-IQ3_S.gguf` (13.7 GB) [S10].
2. **Qwen3.8-27B UD-IQ4_XS.** Repo `unsloth/Qwen3.8-27B-GGUF`, file `Qwen3.8-27B-UD-IQ4_XS.gguf` (14.3 GB) [S11]. If research jobs run out of context, use `Qwen3.8-27B-UD-Q3_K_XL.gguf` (13.1 GB) instead.
3. **Muse Glimmer 30B UD-Q3_K_XL.** Repo `unsloth/Muse-Glimmer-30B-GGUF`, file `Muse-Glimmer-30B-UD-Q3_K_XL.gguf` (13.4 GB) [S13].

Alternate for slot 3 if the always-on reasoning is too slow for chat: Gemma 4 26B-A4B QAT, repo `google/gemma-4-26B-A4B-it-qat-q4_0-gguf`, file `gemma-4-26B_q4_0-it.gguf` (14.4 GB) [S8].

What to check on each first run: the `n_ctx_slot` value and whether any layer went to CPU (llama-server log). Then a tool call through the Web UI with fetch, and one plan job and one research job. For Qwen, look for tool-call text appearing in the reply instead of a tool call [G1].

### Open questions

- None of the shortlisted models has an independent BFCL / TAU score. All comparisons rest on vendor tables.
- Whether #30118 affects the official Qwen3.6 / 3.8 templates, or only the community template the reporter used, is **unverified**.
- 3-bit quality for dense Qwen3.8-27B and Muse Glimmer is unmeasured.
- Real `--fit` context and t/s on this card are unmeasured; section 3–4 numbers are estimates.
- GLM-4.7-Flash and Kolibri-1 were not checked against their model cards.

## Sources

Primary (vendor cards, file listings, llama.cpp source):
- [S1] Qwen organisation page (Qwen3.8 collections, Aug 2026): https://huggingface.co/Qwen
- [S2] Qwen3.6-35B-A3B model card (agent table incl. Qwen3.5-27B, Gemma4 columns; architecture; thinking flags): https://huggingface.co/Qwen/Qwen3.6-35B-A3B
- [S3] Qwen3.5-35B-A3B model card (BFCL-V4, TAU2, IFBench, AA-LCR): https://huggingface.co/Qwen/Qwen3.5-35B-A3B
- [S3a] Qwen3.5-9B model card (9B/4B vs gpt-oss-20b columns): https://huggingface.co/Qwen/Qwen3.5-9B
- [S4] Qwen3.8-27B model card (incl. Muse Glimmer-30B column): https://huggingface.co/Qwen/Qwen3.8-27B
- [S5] Muse Glimmer 30B model card, Meta: https://huggingface.co/meta-models/Muse-Glimmer-30B (config: .../raw/main/config.json)
- [S6] Muse Glimmer 30B GGUF card, Meta (min. build b10353, reasoning cannot be disabled, `<|eom|>` warning, accuracy loss per build): https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF
- [S7] Gemma 4 26B-A4B-it model card, Google (all sizes; Tau2, MRCR; thinking): https://huggingface.co/google/gemma-4-26B-A4B-it; configs: https://huggingface.co/google/gemma-4-31B-it/raw/main/config.json, https://huggingface.co/google/gemma-4-26B-A4B-it/raw/main/config.json, https://huggingface.co/google/gemma-4-12B-it/raw/main/config.json; collections: https://huggingface.co/collections/google/gemma-4, https://huggingface.co/collections/google/gemma-4-qat-q4-0
- [S8] Google QAT GGUF, 26B-A4B (file listing): https://huggingface.co/google/gemma-4-26B-A4B-it-qat-q4_0-gguf
- [S9] Google QAT GGUF, 12B: https://huggingface.co/google/gemma-4-12B-it-qat-q4_0-gguf
- [S10] https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF
- [S11] https://huggingface.co/unsloth/Qwen3.8-27B-GGUF (file names via HF API tree)
- [S12] https://huggingface.co/unsloth/Qwen3.5-9B-GGUF
- [S13] https://huggingface.co/unsloth/Muse-Glimmer-30B-GGUF
- [S14] https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF
- [S15] https://huggingface.co/unsloth/gemma-4-31B-it-GGUF
- [S16] https://huggingface.co/ggml-org/gpt-oss-20b-GGUF
- [S17] Ministral 3 14B Instruct 2512 GGUF, Mistral: https://huggingface.co/mistralai/Ministral-3-14B-Instruct-2512-GGUF
- [S18] Granite 4.2 30B model card, IBM: https://huggingface.co/ibm-granite/granite-4.2-30b ; docs: https://www.ibm.com/granite/docs/models/granite4-2
- [S19] Nemotron 3.5 Lightning 30B-A3B card, NVIDIA (NVIDIA-run comparison table): https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16 ; [S19b] https://huggingface.co/ggml-org/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-GGUF
- [S20] Unsloth, Qwen3.5 GGUF benchmarks (KLD/PPL, Mar 5 2026 update): https://unsloth.ai/docs/models/qwen3.5/gguf-benchmarks
- [S22] BFCL leaderboard (last updated 2026-04-12): https://gorilla.cs.berkeley.edu/leaderboard.html
- [S23] tau-bench site: https://taubench.com/
- [S25] llama.cpp Vulkan scoreboard, discussion #10879: https://github.com/ggml-org/llama.cpp/discussions/10879
- [S30] llama.cpp release b11636 (2026-10-15): https://github.com/ggml-org/llama.cpp/releases/tag/b11636
- [S31] `common/chat.cpp` at `529ac893e`, `common_chat_template_detect_params_init`: https://github.com/ggml-org/llama.cpp/blob/529ac893e/common/chat.cpp (parsers: https://github.com/ggml-org/llama.cpp/tree/529ac893e/common/parsers)
- [S32] `src/llama-arch.cpp` at `529ac893e`: https://github.com/ggml-org/llama.cpp/blob/529ac893e/src/llama-arch.cpp
- [S33] `common/fit.cpp` at `529ac893e` (step 2 reduces context, step 3/4 overflows MoE expert tensors): https://github.com/ggml-org/llama.cpp/blob/529ac893e/common/fit.cpp
- [S34] `docs/function-calling.md` at `529ac893e`: https://github.com/ggml-org/llama.cpp/blob/529ac893e/docs/function-calling.md
- [S35] llama.cpp PR #29307 "chat : implement token aware parsing" (draft): https://github.com/ggml-org/llama.cpp/pull/29307
- [S36] llama.cpp release v0.5.0 = b11146 (Sep 23): https://github.com/ggml-org/llama.cpp/releases/tag/v0.5.0
- [S37] `src/models/gemma4.cpp` at `529ac893e`: https://github.com/ggml-org/llama.cpp/blob/529ac893e/src/models/gemma4.cpp

Independent / community (supporting evidence, not official):
- [S21] oobabooga (localbench), "Gemma 4 and Qwen 3.6 with q8_0 and q4_0 KV cache: KL divergence results" (2026-04-24): https://localbench.substack.com/p/kv-cache-quantization-benchmark
- [S24] llama.cpp discussion #15396, "guide: running gpt-oss with llama.cpp": https://github.com/ggml-org/llama.cpp/discussions/15396
- [S26] 7900 XTX llama-bench for Qwen3.5-35B-A3B: https://huggingface.co/ubergarm/Qwen3.5-35B-A3B-GGUF/discussions/1
- [S27] tcctr/LocalLLM (RX 9060 XT 16 GB, Qwen3.6-35B-A3B, Vulkan): https://github.com/tcctr/LocalLLM
- [S28] llama.cpp discussion #26881 (Muse Glimmer on Vulkan, RX 7900 XT): https://github.com/ggml-org/llama.cpp/discussions/26881

GitHub issues:
- [G1] #30118 Qwen3.6 tool calls leak into content (open): https://github.com/ggml-org/llama.cpp/issues/30118
- [G2] #27767 required/named tool_choice not enforced with thinking off: https://github.com/ggml-org/llama.cpp/issues/27767
- [G4] #29655 Gemma 4 unstable tool calling while streaming (open): https://github.com/ggml-org/llama.cpp/issues/29655
- [G5] #27025 Muse Glimmer peg-native tool-call 500s: https://github.com/ggml-org/llama.cpp/issues/27025
- [G6] #20934 ROCm vs Vulkan tg on 7900 XTX: https://github.com/ggml-org/llama.cpp/issues/20934

Secondary only (used for exclusions, marked Unverified above): Kolibri-1 coverage https://www.datacamp.com/de/blog/aleph-alpha-kolibri-1 ; GLM-4.7-Flash https://unsloth.ai/docs/models/glm-4.7-flash ; Gemma 4 release date https://en.wikipedia.org/wiki/Gemma_(language_model)
