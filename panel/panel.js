(() => {
  "use strict";
  const MAX_CALLS = 40;
  const VISIBLE_MS = 2000, HIDDEN_MS = 15000;
  const STATES = {
    queued: ["Queued", "accent"], running: ["Running", "accent"], awaiting_approval: ["Needs review", "warn"],
    applying: ["Applying", "accent"], restoring: ["Restoring", "accent"], applied: ["Applied", "ok"],
    restored: ["Restored", "ok"], done: ["Done", "ok"], failed: ["Failed", "bad"],
    cancelled: ["Cancelled", "neutral"], rejected: ["Rejected", "neutral"],
  };
  const ACTIVE = new Set(["queued", "running", "applying", "restoring"]);
  const $ = (id) => document.getElementById(id);
  const list = $("jobs"), statusEl = $("status"), flashEl = $("flash"), summary = $("summary");
  const embedded = window.parent !== window;
  let visible = !embedded, busy = false, timer = null, expanded = new Set(), lastJobs = [];

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = String(text);
    return n;
  }

  function apiKey() {
    try { return String(JSON.parse(localStorage.getItem("LlamaUi.config") || "{}").apiKey || "").trim(); }
    catch (e) { return ""; }
  }

  async function api(method, path) {
    const key = apiKey();
    if (!key) { const e = new Error("no key"); e.kind = "no-key"; throw e; }
    let r;
    try { r = await fetch("/agent-jobs/api/" + path, {method, headers: {Authorization: "Bearer " + key}}); }
    catch (err) { const e = new Error("down"); e.kind = "down"; throw e; }
    if (r.status === 403) { const e = new Error("forbidden"); e.kind = "forbidden"; throw e; }
    if (r.status === 502) { const e = new Error("down"); e.kind = "down"; throw e; }
    let data = {};
    try { data = await r.json(); } catch (e) {}
    if (!r.ok) { const e = new Error(data.message || ("HTTP " + r.status)); e.kind = "error"; throw e; }
    return data;
  }

  function showStatus(text) {
    statusEl.textContent = text || "";
    statusEl.hidden = !text;
  }

  function flash(text) {
    flashEl.textContent = text;
    flashEl.hidden = !text;
    if (text) setTimeout(() => { if (flashEl.textContent === text) flashEl.hidden = true; }, 8000);
  }

  function minutes(job) {
    const end = ACTIVE.has(job.state) ? Date.now() / 1000 : job.updated;
    const m = Math.max(0, Math.round((end - job.created) / 60));
    return m < 1 ? "under 1 min" : m + " min";
  }

  function describeOp(op, i) {
    const n = String(i + 1).padStart(3);
    if (op && op.op === "move") return `${n} move    ${op.src}\n          → ${op.dst}`;
    if (op && op.op === "remove") return `${n} remove  ${op.path}   (to trash, restorable)`;
    if (op && op.op === "mkdir") return `${n} mkdir   ${op.path}`;
    return `${n} ?       ${JSON.stringify(op)}`;
  }

  function button(text, onClick) {
    const b = el("button", "", text);
    b.type = "button";
    b.disabled = busy;
    b.addEventListener("click", onClick);
    return b;
  }

  async function act(path, confirmText) {
    if (busy) return;
    if (confirmText && !window.confirm(confirmText)) return;
    busy = true;
    render(lastJobs);
    try {
      const res = await api("POST", path);
      flash(res.message || (res.ok ? "Done." : "That didn't work."));
    } catch (e) {
      flash(e.kind === "down" ? "Agents server isn't running." : e.kind === "forbidden" ? "The API key was refused." : e.message);
    } finally {
      busy = false;
      refresh();
    }
  }

  function stepsList(job) {
    const ul = el("ul", "steps");
    const active = ACTIVE.has(job.state);
    job.steps.forEach((text, i) => {
      const last = i === job.steps.length - 1;
      const li = el("li", active && last ? "current" : "");
      const mark = el("span", "mark" + (active && last ? " spin" : ""), active && last ? "◌" : "✓");
      li.append(mark, el("span", "", text));
      ul.append(li);
    });
    return ul;
  }

  function card(job) {
    const [label, tone] = STATES[job.state] || [job.state, "neutral"];
    const finished = !ACTIVE.has(job.state) && job.state !== "awaiting_approval" && !job.can_restore && !job.queue_count;
    const c = el("article", "card" + (job.state === "awaiting_approval" ? " review" : "") + (finished && !expanded.has(job.id) ? " compact" : ""));
    const head = el("div", "head");
    head.append(el("span", "kind"), el("span", "title", job.title || job.kind), el("span", "chip " + tone, label));
    c.append(head);
    if (finished && !expanded.has(job.id)) {
      c.title = "Show details";
      c.addEventListener("click", () => { expanded.add(job.id); render(lastJobs); });
      return c;
    }
    if (job.steps && job.steps.length) c.append(stepsList(job));
    if (ACTIVE.has(job.state) || job.step_count) {
      c.append(el("div", "meta", `${job.step_count} of ${MAX_CALLS} steps · ${minutes(job)}`));
    }
    if (job.note) c.append(el("div", "note", job.note));
    if (job.state === "awaiting_approval") {
      if (Array.isArray(job.plan)) {
        c.append(el("div", "plan", job.plan.map(describeOp).join("\n")));
        if (job.review) c.append(el("div", "review-text", "Review: " + job.review.trim()));
        const a = el("div", "actions");
        a.append(button(`Approve ${job.plan.length} changes`, () => act(`jobs/${job.id}/apply`)),
                 button("Reject", () => act(`jobs/${job.id}/reject`)));
        c.append(a);
      } else {
        c.append(el("div", "note", "The plan file couldn't be read, so it can't be approved here."));
      }
    }
    if (job.can_restore) {
      const a = el("div", "actions");
      a.append(button("Restore", () => act(`jobs/${job.id}/restore`,
        "Put every file this job moved or removed back where it was?")));
      c.append(a);
    }
    if (job.queue_count) {
      const a = el("div", "actions");
      a.append(button(`Queue ${job.queue_count} follow-up jobs`, () => act(`jobs/${job.id}/queue`)));
      c.append(a);
    }
    return c;
  }

  function render(jobs) {
    lastJobs = jobs;
    list.replaceChildren();
    if (!jobs.length) {
      list.append(el("div", "empty", "No agent jobs yet. Ask the chat to research, plan or organise something."));
    } else {
      jobs.forEach((j) => list.append(card(j)));
    }
    const running = jobs.filter((j) => ACTIVE.has(j.state)).length;
    const review = jobs.filter((j) => j.state === "awaiting_approval").length;
    summary.textContent = [running && `${running} running`, review && `${review} to review`].filter(Boolean).join(" · ");
    $("empty-trash").disabled = busy;
    if (embedded) window.parent.postMessage({type: "agent-jobs-counts", running, review}, location.origin);
  }

  async function refresh() {
    clearTimeout(timer);
    try {
      const data = await api("GET", "jobs");
      showStatus("");
      if (!busy) render(Array.isArray(data.jobs) ? data.jobs : []);
    } catch (e) {
      showStatus(e.kind === "no-key" ? "Paste your API key in the Web UI under Settings → General, then reopen this panel."
        : e.kind === "down" ? "Agents server isn't running. Start the servers from the launcher."
        : e.kind === "forbidden" ? "The API key was refused. Copy it again from the launcher and paste it in Settings → General."
        : "Couldn't load jobs: " + e.message);
    }
    clearTimeout(timer);  // overlapping refreshes (action, visibility change) must not leave two poll loops running
    timer = setTimeout(refresh, visible ? VISIBLE_MS : HIDDEN_MS);
  }

  $("empty-trash").addEventListener("click", () => act("trash/empty",
    "Permanently delete everything in the agent trash? Jobs whose files are in there can no longer be restored."));
  window.addEventListener("message", (e) => {
    if (e.origin !== location.origin || e.source !== window.parent) return;
    const d = e.data || {};
    if (d.type !== "agent-jobs-visible") return;
    const was = visible;
    visible = !!d.visible;
    if (visible && !was) refresh();
  });
  refresh();
})();
