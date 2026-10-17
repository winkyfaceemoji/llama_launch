---
paths: eval/**
---

# eval/

- **The agents score is the mean of the area percentages**, so an area with one case swings it by a full 20 points. Plan had only P1 until 2026-10-16, and one 900 s timeout put Qwen3.6-35B-A3B fourth. Keep every area at two or more cases (plan has P1-P3).
- **Tool-choice prompts must accept every right answer.** Asking to sort a folder the agents can't read yet is correctly answered with `request_folder_access`; T10 once expected only `organize`, while T13 already accepted both, and 4 of 5 models "failed" it.
- **Implicit research prompts must signal a background job** ("a report I can read later", "I'll check back"). "Deep dive with sources" alone is fairly answered in chat with the search tool, and every model did, so T15/T16 measured nothing.
- **Each model test result keeps the raw stress output under `stress`** (plans, reports, notes, tool calls). The next test deletes the previous test's jobs, so this is the only place to check whether a failure was the model's or the test's.
- **Changing cases, prompts, questions or scoring breaks comparison with older results: bump `TEST_VERSION` in `model_test.py`.** The report averages repeat runs per model (mean and min-max) only within the newest version and lists older versions apart; results from before 2026-10-17 have no version and count as 1. Rerun the models you are comparing after a bump.
- **One run can't rank close models.** The same model moved 7-9 points between runs (Qwen3.6 agents 69/71/78, Ministral reasoning 70/57.5), so compare averages of two or more runs.
- **Reasoning answers are read leniently except for `instructions`:** LaTeX (`\(\frac{1}{9}\)`, `\boxed{}`) is unwrapped, and a sentence counts when it holds exactly one date, time, weekday or number that matches ("Thus, 1 May 2025 was a Thursday."). Two candidates of a type ("Tuesday or Thursday") fail. Instruction questions stay strict: the format is what they test.
- **Acting on a named job may start by looking it up** (`LOOK_UP` in `stress_agents.py`: `job_status`/`job_result`), and a link written alone in backticks is a citation (`runner.cited_urls`); both were scored as failures before version 2.
