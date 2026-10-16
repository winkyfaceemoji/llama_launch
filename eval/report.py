"""Test report: eval/results/report.html, one row per complete model test, built from every result file.

model_test.main() rebuilds it after each run. Model names, answers and reasons are untrusted text, so every
string from a result file goes through html.escape. Self-contained: inline CSS, no scripts, nothing remote.
"""
import glob
import html
import json
import os

RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
COLUMNS = ("Model", "Build", "Date", "Agents %", "Reasoning %", "Gen tok/s", "Context")
NOTE = ("Agents % is the mean of the per-area pass rates and Reasoning % the mean of the per-kind ones, each area "
        "and kind weighted equally. Scores move a few points between runs because each model uses its own "
        "sampling defaults.")

CSS = """
:root{--bg:#fff;--fg:#1a1a1a;--muted:#666;--line:#ddd;--alt:#f5f5f5;--ok:#1a7f37;--bad:#c62828;--warn:#9a6700}
@media (prefers-color-scheme:dark){:root{--bg:#161616;--fg:#e8e8e8;--muted:#999;--line:#333;--alt:#202020;
--ok:#4cc26a;--bad:#ff6b6b;--warn:#e0b040}}
body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
h1{font-size:1.3em;margin:0 0 .3em}h3{font-size:1em;margin:1em 0 .3em}
p.note{color:var(--muted);margin:0 0 1em;max-width:60em}
.row{display:grid;grid-template-columns:minmax(10em,3fr) repeat(6,minmax(4.5em,1fr));gap:.5em;padding:.5em;
border-bottom:1px solid var(--line);overflow-wrap:anywhere}
.head{font-weight:600;color:var(--muted)}
summary{cursor:pointer;list-style:none}summary::-webkit-details-marker{display:none}
summary .model::before{content:"\\25B8  "}details[open] summary .model::before{content:"\\25BE  "}
details[open] summary{background:var(--alt)}
.body{padding:.2em .8em 1em;border-bottom:1px solid var(--line)}
.unsafe{color:#fff;background:var(--bad);border-radius:3px;padding:0 .4em;font-size:.85em;font-weight:600}
table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:.2em .5em;border-bottom:1px solid var(--line);
vertical-align:top;overflow-wrap:anywhere}
.pass{color:var(--ok)}.fail{color:var(--bad)}.cut{color:var(--warn)}
ul{margin:.2em 0;padding-left:1.2em}
@media (max-width:640px){.head{display:none}.row{grid-template-columns:repeat(3,1fr)}.row .model{grid-column:1/-1}
.row span[data-l]::before{content:attr(data-l) " ";color:var(--muted);font-size:.85em}}
"""


def _text(v):
    return "–" if v is None or v == "" else html.escape(str(v))


def _percents(groups):
    return "<ul>" + "".join(f"<li>{_text(name)}: {_text(g.get('percent'))}%</li>"
                            for name, g in (groups or {}).items()) + "</ul>"


def _expected(check):
    if isinstance(check, dict):
        return "; ".join(f"{k}: {v}" for k, v in check.items())
    return check


def _details(r):
    parts = ["<h3>Agents by area</h3>", _percents(r.get("areas")), "<h3>Reasoning by kind</h3>",
             _percents(r.get("kinds")), "<h3>Cases</h3><table><tr><th>Area</th><th>Case</th><th>Result</th>"
             "<th>Why</th></tr>"]
    for area, g in (r.get("areas") or {}).items():
        for case, ok, why in g.get("cases") or []:
            res = '<span class="pass">pass</span>' if ok else '<span class="fail">fail</span>'
            parts.append(f"<tr><td>{_text(area)}</td><td>{_text(case)}</td><td>{res}</td><td>{_text(why)}</td></tr>")
    parts.append("</table>")
    wrong = r.get("wrong") or []
    if wrong:
        parts.append("<h3>Wrong answers</h3><table><tr><th>Kind</th><th>Question</th><th>Expected</th>"
                     "<th>Given</th></tr>")
        for w in wrong:
            given = _text(w.get("given"))
            if w.get("cut_off"):  # ran out of room, not a wrong answer
                given = f'<span class="cut">cut off at the token cap</span><br>{given}'
            elif not str(w.get("given") or "").strip():  # ended without a final answer at all
                given = '<span class="cut">no answer</span>'
            parts.append(f"<tr><td>{_text(w.get('kind'))}</td><td>{_text(w.get('q'))}</td>"
                         f"<td>{_text(_expected(w.get('expected')))}</td><td>{given}</td></tr>")
        parts.append("</table>")
    if r.get("unsafe_reasons"):
        parts.append("<h3>Safety</h3><ul>" + "".join(f"<li>{_text(x)}</li>" for x in r["unsafe_reasons"]) + "</ul>")
    return "".join(parts)


def _row(r):
    mark = ' <span class="unsafe">unsafe</span>' if r.get("unsafe") else ""
    cells = [r.get("build"), (r.get("started") or "")[:10], r.get("agents_score"), r.get("reasoning_score"),
             (r.get("speed") or {}).get("predicted_per_second"), r.get("context")]
    spans = "".join(f'<span data-l="{l}">{_text(c)}</span>' for l, c in zip(COLUMNS[1:], cells))
    return (f'<details><summary class="row"><span class="model">{_text(r.get("model"))}{mark}</span>{spans}'
            f'</summary><div class="body">{_details(r)}</div></details>')


def render(runs):
    """The report page for these result dicts, best agents score first."""
    runs = sorted(runs, key=lambda r: r.get("agents_score") or 0, reverse=True)
    head = "".join(f"<span>{c}</span>" for c in COLUMNS)
    rows = "".join(_row(r) for r in runs) or "<p>No complete model tests yet.</p>"
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>Model test report</title><style>{CSS}</style></head><body><h1>Model test report</h1>"
            f'<p class="note">{NOTE}</p><div class="row head">{head}</div>{rows}</body></html>')


def build(results_dir=RESULTS):
    """Write results_dir/report.html from every model test result there; returns its path."""
    runs = []
    for p in sorted(glob.glob(os.path.join(results_dir, "*.json"))):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("kind") == "model_test":  # stress_agents results have no kind
            runs.append(d)
    path = os.path.join(results_dir, "report.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(render(runs))
    return path
