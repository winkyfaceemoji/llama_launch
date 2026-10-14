# Local coding agents on a 16 GB GPU (October 2026)

Research date: 2026-10-14. Target setup: RX 7800 XT 16 GB, 32 GB RAM, Windows 11, llama.cpp b9776
(commit `ac4105d68`, published 2026-06-24 [S25]), Vulkan, `--fit`, flash-attn, q8_0 KV cache, Cline 3.89.2, Continue 2.0.0.
All benchmark numbers are as published by the source named; "vendor" means the model's own publisher ran it.

## Bottom line

- Vendor-reported SWE-bench Verified for open models with a GGUF of 16 GB or less: Devstral Small 2 (24B) 68.0% [S1], gpt-oss-20b 60.7% at high reasoning [S8], Qwen3.6-27B 77.2% [S2]. A year earlier, the same leaderboard's best 24B entry was Devstral Small 1.0 at 46.8% (OpenHands, 2025-05-20) [S11].
- Independent scoring disagrees with vendor numbers. On swebench.com's mini-SWE-agent (bash-only) board, Devstral Small 2512 scores 56.4% against Mistral's 68.0%. On the same board Claude 4.5 Opus (high) scores 76.8% [S11][S1].
- Terminal-Bench 2.0: Devstral Small 2 22.5% against GPT-5.1 Codex Max 60.4% (Mistral's table) [S1]. Qwen's tables put Qwen3.6-27B at 59.3%, equal to its Claude 4.5 Opus figure [S2]. The newest frontier figure found is GPT-5.5 at 83.4% on Terminal-Bench 2.1 with Codex CLI [S15].
- Client side: Cline's own Sept-2025 write-up of AMD's tests says gpt-oss-20b failed, and that models smaller than Qwen3 Coder 30B "consistently fail" with Cline [S19]. Cline cannot read the context size from llama-server and falls back to 128K, which breaks auto-compaction unless the size is set by hand [S17][G1].
- 16 GB budget: Devstral Small 2 Q4_K_M (14.3 GB) plus a 32K q8_0 KV cache comes to about 16.0 GiB before compute buffers, so `--fit` will cut context or offload layers. The hybrid Qwen3.5/3.6/3.8 models need about 34 KiB of KV per token at 27B, against 85 KiB for Devstral. No primary tokens/sec measurement for any of these models on a 7800 XT exists.

## 1-2. Models x agentic benchmarks

"Fits" = GGUF file size (decimal GB, Hugging Face listing) against 16 GiB VRAM (17.2 GB) minus the default 1024 MiB `--fit` margin [S24]. KV and compute buffers are extra; see section 4.

| Model (release) | GGUF size | SWE-bench Verified | Terminal-Bench | Other agentic | Independent | Loads on b9776? |
|---|---|---|---|---|---|---|
| Devstral Small 2 24B (2512, Dec 2025) [S1] | Q4_K_M 14.3, IQ4_XS 12.8, Q3_K_M 11.5 [S27a] | 68.0 (vendor; scaffold not stated on card) [S1] | TB2 22.5 (vendor) [S1] | SWE-bench Multilingual 55.7 [S1] | mini-SWE-agent v1.17.2: 56.4 (2025-12-09) [S11] | Card requires llama.cpp PR #17945 (Dec 2025); b9776 is later [S1][S25] |
| Qwen3-14B (May 2025) [S7] | Q4_K_M 9.0 [S27b] | none published | none published | BFCL v3 70.4 (thinking) / 61.5 (non-thinking); LiveCodeBench v5 63.5 / 29.0 [S7] | none on swebench.com [S11] or aider [S12] | yes (predates build) |
| Qwen3.5-9B (Feb 2026) [S6] | Q4_K_M 5.68, Q8_0 9.53 [S27c] | none on card [S6] | none on card [S6] | BFCL-V4 66.1, TAU2 79.1, LiveCodeBench v6 65.6 [S6] | none found | yes (predates build) |
| Qwen3.5-27B (Feb 2026) | not checked | 75.0 (Qwen's internal scaffold) [S3] | TB2 41.6 (Terminus-2) [S3] | SWE-bench Pro 51.2 [S3] | none found | yes (predates build) |
| Qwen3.6-27B (Apr 2026) [S2] | Q4_K_M 16.8, IQ4_XS 15.4, Q3_K_M 13.6 [S27d] | 77.2 (internal scaffold: bash + file-edit tools, 200K ctx) [S2] | TB2 59.3 (Harbor/Terminus-2, avg of 5) [S2] | SWE-bench Pro 53.5, Multilingual 71.3 [S2] | none found | yes (predates build) |
| Qwen3.6-35B-A3B MoE (Apr 2026) [S3] | UD-IQ4_XS 17.7, UD-Q3_K_M 16.6, UD-Q4_K_M 22.1 [S27e]; needs expert offload to RAM | 73.4 (same internal scaffold) [S3] | TB2 51.5 (Terminus-2) [S3] | SWE-bench Pro 49.5 [S3] | none found | yes (predates build) |
| Qwen3.8-27B (Aug 14 2026) [S4][S5] | UD-IQ4_XS 14.3, UD-Q3_K_XL 13.1, UD-Q4_K_M 16.5 [S27f] | not reported on card [S4] | TB2.1 73.0 (Terminus) [S4] | SWE-bench Pro 61.7 (Claude Code harness) [S4] | none found | **Unverified.** Released after b9776. GGUF arch is `qwen35`, same family as Qwen3.5 [S27f] |
| gpt-oss-20b (Aug 2025) [S8] | MXFP4 12.11 [S27g]; card: runs "within 16GB of memory" [S9] | 37.4 / 53.2 / 60.7 (low/med/high; harness not named) [S8] | none published | Aider polyglot 16.6 / 26.6 / 34.2 [S8] | 20b not listed; gpt-oss-120b scores 26.0 on mini-SWE-agent v1.7.0 against OpenAI's 62.4 [S11][S8] | yes (predates build) |
| Qwen3-Coder-30B-A3B (Jul 2025) | Q4_K_M 18.56, Q3_K_M 14.71 [S27h] | OpenHands: 51.6 (2025-08-05) [S11] | none found | Cline's and Continue's recommended local model [S18][S20] | same row | yes |
| "Codestral 2 (22B)" | n/a | **No such model found.** Mistral lists Codestral 25.08 (API, "Premier") and the deprecated 25.01/24.05; no "Codestral 2" [S10] | n/a | Aider polyglot: Codestral 25.01 11.1% (2025-01-13) [S12] | n/a | n/a |

Notes:
- Qwen's tables also give Gemma4-31B 52.0 and Gemma4-26B-A4B 17.4 on SWE-bench Verified, run under Qwen's own scaffold [S3].
- Mistral's newer open model, Mistral Small 4, is 119B MoE and is listed as the replacement for the deprecated Devstral API models, so it does not fit [S10].
- Quantisation effects are **unknown** for every row. No publisher measured Q3/Q4 GGUF quality. Qwen ships only FP8 as an official reduced-precision variant [S5].
- Benchmark caveat: OpenAI stopped reporting SWE-bench Verified on 2026-02-23, citing contamination and underspecified tests, and recommends SWE-bench Pro instead [S13]. Anthropic's Opus 4.7 post notes memorisation screens on Verified and Pro [S14].
- The Aider leaderboard was last updated 2025-11-20 and has no Qwen3.5+, Devstral or gpt-oss-20b entries [S12].

## 3. Tool calling in Cline and Continue with local models

**Cline (official docs and blog)**
- Supported local runtimes in the docs are Ollama, LM Studio and Atomic Chat. llama-server is reached through the generic "OpenAI Compatible" provider, which has manual "Context Window size" and "Max Output Tokens" fields [S16][S17].
- The docs list "Use Compact Prompt" as a recommended setting for local inference. They give no model list and no minimum context [S16].
- Cline blog (2025-08-28): the compact prompt is about 10% of the full system prompt and drops MCP tools and Focus Chain. The recommended model is Qwen3 Coder 30B A3B at 262,144 context. It says to keep KV-cache quantisation off in LM Studio [S18].
- Cline blog on AMD's tests (2025-09-30): AMD tested 20+ models and "only a handful actually work reliably". The 32 GB-RAM tier uses Qwen3 Coder 30B 4-bit at 32K context with compact prompt on. gpt-oss-20b, seed-oss-36b and deepseek-r1-0528-qwen3-8b failed. Smaller models than Qwen3 Coder 30B "consistently fail". Flash attention is "essential" on AMD, and KV-cache quantisation should stay off [S19].
- No Cline document found covers Devstral Small 2, Qwen3.5/3.6/3.8 or llama-server specifically.
- Devstral Small 2's own card lists Cline as a compatible scaffold [S1].

**Continue (official docs)**
- Agent mode uses the Chat model if it supports tool calling. It falls back to "system message tools", where tools are injected as XML into the system prompt and parsed back out, which the docs say can be more reliable for weak tool-callers [S20].
- Recommended open models for the agent role are Qwen3 Coder 480B and 30B, "Devstral (27B)" and Kimi K2. The docs say closed models are "slightly better" [S20].
- Capabilities are autodetected by provider and model name. For custom deployments the docs advise declaring `capabilities: [tool_use]` by hand, and a manual setting can only add capabilities, not remove them [S21].
- The `llama.cpp` provider page shows only `apiBase: http://localhost:8080` and says nothing about tools or agent mode [S22].
- No minimum context is documented [S20].

**llama.cpp side (docs at the user's commit)**
- `--jinja` is on by default [S24]. Native tool-call parsers exist for several families. Unknown templates use a "Generic" format that "may consume more tokens" [S23].
- The docs warn: "Beware of extreme KV quantizations (e.g. `-ctk q4_0`)", which can degrade tool calling. q8_0 is not named [S23].

**GitHub issues (supporting evidence only, not official)**
- [G1] cline#7772 (Nov 2025, closed not planned). Auto-compact failed with gpt-oss-120b on llama-server. A bot reply says Cline cannot read llama-server's context size and assumes 128K.
- [G2] cline#9651 (open, Mar 2026). A tool schema of about 4.5K tokens plus 3K–14K tokens of system prompt is sent each request.
- [G3] cline#7048 (Oct 2025). Qwen3 30B with a 32K window filled about 27K after the first round.
- [G4] continue#5909 (closed not planned, Sep 2025): "Support Agent mode with Llama.cpp provider".
- [G5] continue#11453 (closed not planned, Sep 2026). Agent tool calls fail when an OpenAI-compatible server returns XML-like `function.arguments` (Qwen/Hermes-style).
- [G6] continue#5696 (open). A commenter's tools failed under `provider: llama.cpp` and worked after switching provider.

## 4. Practical limits at 16 GB

**KV cache per token at q8_0.** Formula: 2 × attention layers × KV heads × head_dim × 1.0625 bytes, where a ggml q8_0 block is 32 int8 values plus one fp16 scale [S29]. Dimensions come from each model's `config.json` [S28]. Linear-attention (Gated DeltaNet) layers keep a fixed-size state and are excluded.

| Model | Attention layers used | KiB/token | 32K ctx | 64K ctx | 128K ctx | Weights (GiB) |
|---|---|---|---|---|---|---|
| Devstral Small 2 | 40 full, 8 KV x 128 | 85 | 2.66 GiB | 5.31 | 10.6 | Q4_K_M 13.3 |
| Qwen3-14B (native ctx 40,960) | 40 full, 8 x 128 | 85 | 2.66 | n/a natively | n/a | Q4_K_M 8.4 |
| Qwen3.5-9B | 8 of 32, 4 x 256 | 17 | 0.53 | 1.06 | 2.13 | Q4_K_M 5.3 |
| Qwen3.6-27B / 3.8-27B | 16 of 64, 4 x 256 | 34 | 1.06 | 2.13 | 4.25 | Q3_K_M 12.7 / UD-Q3_K_XL 12.2 |
| Qwen3.6-35B-A3B | 10 of 40, 2 x 256 | 10.6 | 0.33 | 0.66 | 1.33 | UD-IQ4_XS 16.5 (exceeds VRAM) |
| gpt-oss-20b | 12 full + 12 sliding (128) | 12.75 | 0.40 | 0.80 | 1.59 | MXFP4 11.3 |

- The budget is 16 GiB minus the default 1024 MiB `--fit` margin, minus compute buffers and desktop use (not quantified here). `--fit` is on by default and can lower context down to `--fit-ctx`, default 4096 [S24]. Devstral Small 2 Q4_K_M at 32K comes to about 15.98 GiB of weights plus KV, over the about-15 GiB budget.
- MoE models above VRAM size (Qwen3.6-35B-A3B, Qwen3-Coder-30B-A3B) rely on `--cpu-moe` / `--n-cpu-moe` to keep expert weights in system RAM [S24]. The speed effect on this machine is **unknown**.
- Client context needs:
  - Cline's AMD-tested 32 GB-RAM tier uses 32K context with compact prompt [S19].
  - Cline's main local recommendation is 262K [S18].
  - Cline issue reports put the full prompt plus tools at roughly 7.5K–18.5K tokens per request [G2].
  - Continue documents no minimum [S20].

**Speed (tokens/sec).** No primary measurement exists for any listed model on a 7800 XT with Vulkan.
- The only primary anchor is the llama.cpp Vulkan scoreboard. Llama-2 7B Q4_0 (about 3.56 GiB) on an RX 7800 XT with FA on gives pp512 2197 t/s and tg128 124.9 t/s, on an older commit (`4fdbc1e`) [S26].
- Scaling generation by weight size (generation is memory-bandwidth bound) gives rough ceilings: about 33 t/s for Devstral Q4_K_M and about 53 t/s for Qwen3-14B Q4_K_M. **These are not measurements.** Hybrid DeltaNet and MoE-offload kernels on Vulkan are not covered by the anchor.
- Prompt processing for long contexts is **unknown** at 24B–27B. If pp fell in proportion to size (roughly 650 t/s for 24B, unmeasured), a cold 20K-token Cline first request would take about 30 s.
- llama-server's prompt cache is on by default and re-evaluates only the unseen suffix of a matching prefix [S24], so later turns process only the new tokens.

## 5. Frontier comparison (same benchmarks)

| Benchmark | Frontier figure | Local-class figure | Source and harness |
|---|---|---|---|
| SWE-bench Verified, independent same harness | Claude 4.5 Opus (high) 76.8 (mini-SWE-agent 2.0.0, 2026-02-17) | Devstral Small 2512 56.4 (v1.17.2, 2025-12-09) | swebench.com data [S11]; agent versions differ |
| SWE-bench Verified, Qwen's table | Claude 4.5 Opus 80.9 | Qwen3.6-27B 77.2, 35B-A3B 73.4 | Qwen internal scaffold [S2] |
| SWE-bench Verified, Mistral's table | Claude Sonnet 4.5 77.2, GPT-5.1 Codex Max 77.9 | Devstral Small 2 68.0 | [S1] |
| Terminal-Bench 2.0 | Claude 4.5 Opus 59.3 [S2]; GPT-5.1 Codex Max 60.4, Gemini 3 Pro 54.2 [S1] | Qwen3.6-27B 59.3 [S2]; Devstral Small 2 22.5 [S1] | Terminus-2 (Qwen); not stated (Mistral) |
| Terminal-Bench 2.1 | GPT-5.5 83.4 (Codex CLI) [S15]; "Opus4.6 Max" 78.2 [S4] | Qwen3.8-27B 73.0 [S4] | Terminus-2 public harness (Anthropic) [S15] |
| SWE-bench Pro | "Opus4.6 Max" 53.4 [S4]; Claude 4.5 Opus 57.1 [S2] | Qwen3.8-27B 61.7 [S4]; Qwen3.6-27B 53.5 [S2] | Qwen: Claude Code harness for 3.8 [S4] |

- Anthropic's Opus 4.7 (2026-04-16) and Opus 4.8 (2026-05-28) posts put their benchmark tables in images, so the fetcher could not extract the numbers [S14][S15]. No Opus 4.8 SWE-bench figure is quoted here.
- The tbench.ai leaderboard rendered no rows to the fetcher, so no leaderboard-run (non-vendor) Terminal-Bench figure for small models was obtained.

## Sources

Primary:
- [S1] Devstral Small 2 model card, Mistral AI: https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512
- [S2] Qwen3.6-27B model card, Qwen: https://huggingface.co/Qwen/Qwen3.6-27B
- [S3] Qwen3.6-35B-A3B model card, Qwen (includes Qwen3.5-27B/35B-A3B and Gemma 4 comparison columns): https://huggingface.co/Qwen/Qwen3.6-35B-A3B
- [S4] Qwen3.8-27B model card, Qwen: https://huggingface.co/Qwen/Qwen3.8-27B
- [S5] Qwen organisation page (Qwen3.8 collection dated Aug 12–14 2026; FP8 variants): https://huggingface.co/Qwen
- [S6] Qwen3.5-9B model card: https://huggingface.co/Qwen/Qwen3.5-9B (blog: https://qwen.ai/blog?id=qwen3.5)
- [S7] Qwen3 Technical Report, arXiv 2505.09388 (14 May 2025), Tables 15–16: https://arxiv.org/abs/2505.09388
- [S8] gpt-oss-120b & gpt-oss-20b Model Card, arXiv 2508.10925 (Aug 2025), Table 3: https://arxiv.org/abs/2508.10925
- [S9] gpt-oss-20b model card ("within 16GB of memory", MXFP4): https://huggingface.co/openai/gpt-oss-20b
- [S10] Mistral models overview (Codestral 25.08; Devstral deprecations; Mistral Small 4): https://docs.mistral.ai/getting-started/models/models_overview/
- [S11] SWE-bench leaderboards, https://www.swebench.com/. Data file: https://github.com/SWE-bench/swe-bench.github.io/blob/master/data/leaderboards.json (last commit 2026-09-01; newest Verified entry 2026-02-26)
- [S12] Aider LLM leaderboards (last updated 2025-11-20): https://aider.chat/docs/leaderboards/
- [S13] OpenAI, "Why we no longer evaluate SWE-bench Verified" (2026-02-23): https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/ (returned 403 to the fetcher; date and claims taken from the search-index summary of this page)
- [S14] Anthropic, Claude Opus 4.7 (2026-04-16): https://www.anthropic.com/news/claude-opus-4-7
- [S15] Anthropic, Claude Opus 4.8 (2026-05-28), Terminal-Bench 2.1 footnote: https://www.anthropic.com/news/claude-opus-4-8
- [S16] Cline docs, Local models: https://docs.cline.bot/running-models-locally/overview
- [S17] Cline docs, OpenAI Compatible provider: https://docs.cline.bot/provider-config/openai-compatible
- [S18] Cline blog, local coding stack with Qwen3 Coder 30B (2025-08-28): https://cline.bot/blog/local-models
- [S19] Cline blog, "Which local models actually work with Cline? AMD tested" (2025-09-30): https://cline.bot/blog/local-models-amd
- [S20] Continue docs, Model Setup for Agent Mode: https://docs.continue.dev/ide-extensions/agent/model-setup
- [S21] Continue docs, Model Capabilities: https://docs.continue.dev/customize/deep-dives/model-capabilities
- [S22] Continue docs, llama.cpp provider: https://docs.continue.dev/customize/model-providers/more/llamacpp
- [S23] llama.cpp function-calling doc at `ac4105d68`: https://github.com/ggml-org/llama.cpp/blob/ac4105d68b2955027115cf9bb50941ccf56974eb/docs/function-calling.md
- [S24] llama-server README at `ac4105d68` (`--fit`, `--fit-target`, `--fit-ctx`, `--cpu-moe`, `--jinja`, `--cache-prompt`): https://github.com/ggml-org/llama.cpp/blob/ac4105d68b2955027115cf9bb50941ccf56974eb/tools/server/README.md
- [S25] llama.cpp release b9776 (2026-06-24): https://github.com/ggml-org/llama.cpp/releases/tag/b9776
- [S26] llama.cpp Vulkan performance scoreboard discussion #10879: https://github.com/ggml-org/llama.cpp/discussions/10879
- [S27] GGUF file listings:
  - a. https://huggingface.co/unsloth/Devstral-Small-2-24B-Instruct-2512-GGUF
  - b. https://huggingface.co/unsloth/Qwen3-14B-GGUF
  - c. https://huggingface.co/unsloth/Qwen3.5-9B-GGUF
  - d. https://huggingface.co/unsloth/Qwen3.6-27B-GGUF
  - e. https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF
  - f. https://huggingface.co/unsloth/Qwen3.8-27B-GGUF
  - g. https://huggingface.co/ggml-org/gpt-oss-20b-GGUF
  - h. https://huggingface.co/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF
- [S28] `config.json` of each model repo above (e.g. https://huggingface.co/Qwen/Qwen3.6-27B/blob/main/config.json)
- [S29] ggml q8_0 block layout: https://github.com/ggml-org/llama.cpp/blob/ac4105d68b2955027115cf9bb50941ccf56974eb/ggml/src/ggml-common.h

GitHub issues (supporting evidence, not official):
- [G1] https://github.com/cline/cline/issues/7772
- [G2] https://github.com/cline/cline/issues/9651
- [G3] https://github.com/cline/cline/issues/7048
- [G4] https://github.com/continuedev/continue/issues/5909
- [G5] https://github.com/continuedev/continue/issues/11453
- [G6] https://github.com/continuedev/continue/issues/5696
