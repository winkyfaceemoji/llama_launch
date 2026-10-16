---
paths: eval/**
---

# eval/

- **The agents score is the mean of the area percentages**, so an area with one case swings it by a full 20 points. Plan had only P1 until 2026-10-16, and one 900 s timeout put Qwen3.6-35B-A3B fourth. Keep every area at two or more cases (plan has P1-P3).
- **Tool-choice prompts must accept every right answer.** Asking to sort a folder the agents can't read yet is correctly answered with `request_folder_access`; T10 once expected only `organize`, while T13 already accepted both, and 4 of 5 models "failed" it.
- **Implicit research prompts must signal a background job** ("a report I can read later", "I'll check back"). "Deep dive with sources" alone is fairly answered in chat with the search tool, and every model did, so T15/T16 measured nothing.
- **Each model test result keeps the raw stress output under `stress`** (plans, reports, notes, tool calls). The next test deletes the previous test's jobs, so this is the only place to check whether a failure was the model's or the test's.
- **Changing cases or prompts breaks comparison with older results.** Rerun the models you are comparing after such a change.
