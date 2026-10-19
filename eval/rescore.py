"""Rescore saved model test results with the current rules, from the raw output they kept.

python eval/rescore.py rescores every result in eval/results that has a "stress" key and an older
test_version: tool choice against today's TOOL_PROMPTS, the agent cases with today's score_cases, and the
saved final answers with today's check_answer. The original file moves to eval/results/v<old version>/
unchanged, and the rescored one takes its place with "rescored_from". Results without raw output (before
2026-10-16 15:17) can't be rescored; rerun those models.
"""
import copy
import glob
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import model_test  # noqa: E402
import report  # noqa: E402
import stress_agents  # noqa: E402


def rescore(result, name):
    """A rescored copy of one saved result, or None when it kept no raw output to rescore from."""
    if not result.get("stress"):
        return None
    out = copy.deepcopy(result)
    stress = out["stress"]
    expected = {prompt: exp for _, prompt, exp in stress_agents.TOOL_PROMPTS}
    for t in stress.get("tool_choice") or []:
        if t.get("kind") == "distractor" or t.get("prompt") not in expected:
            continue  # distractor rules haven't changed; an unknown prompt keeps its saved verdict
        exp = expected[t["prompt"]]
        t["expected"], t["ok"] = sorted(exp), bool(t.get("calls")) and t["calls"][0] in exp
    areas = model_test.score_cases(stress)
    unsafe, reasons = model_test.safety(stress)
    questions = model_test.load_questions()
    kinds, still_wrong = {}, set()
    for kind, group in (result.get("kinds") or {}).items():
        items = []
        for qid, ok, given in group.get("questions") or []:
            q = questions[int(qid[1:]) - 1]
            ok = ok or model_test.check_answer(given, q["check"], kind)
            items.append((qid, ok, given))
            if not ok:
                still_wrong.add(q["q"])
        kinds[kind] = items
    out.update(test_version=model_test.TEST_VERSION, rescored_from=name,
               areas={a: {"percent": model_test.percent(i), "cases": i} for a, i in areas.items()},
               agents_score=model_test.area_score(areas),
               kinds={k: {"percent": model_test.percent(i), "questions": i} for k, i in kinds.items()},
               reasoning_score=model_test.area_score(kinds),
               wrong=[w for w in result.get("wrong") or [] if w.get("q") in still_wrong],
               unsafe=unsafe, unsafe_reasons=reasons)
    return out


def main():
    for path in sorted(glob.glob(os.path.join(model_test.RESULTS, "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                result = json.load(f)
        except (OSError, ValueError):
            continue
        version = result.get("test_version", 1) if isinstance(result, dict) else None
        if not isinstance(result, dict) or result.get("kind") != "model_test" or version >= model_test.TEST_VERSION:
            continue
        name = os.path.basename(path)
        new = rescore(result, name)
        if new is None:
            print(f"[skip] {name}: no raw output kept; rerun {result.get('model')}", flush=True)
            continue
        keep = os.path.join(model_test.RESULTS, f"v{version}")
        os.makedirs(keep, exist_ok=True)
        shutil.move(path, os.path.join(keep, name))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(new, f, indent=2, ensure_ascii=False)
        print(f"[rescored] {name}: agents {result.get('agents_score')} -> {new['agents_score']}, "
              f"reasoning {result.get('reasoning_score')} -> {new['reasoning_score']}", flush=True)
    print(f"[report] {report.build(model_test.RESULTS)}", flush=True)


if __name__ == "__main__":
    main()
