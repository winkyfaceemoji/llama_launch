(() => {
  "use strict";
  const MAX_CALLS = 40;
  const VISIBLE_MS = 2000, HIDDEN_MS = 15000, TRASH_ARM_MS = 5000, TRASH_CONFIRM_DELAY_MS = 400;
  const STATES = {
    queued: ["Queued", "accent"], running: ["Running", "accent"], awaiting_approval: ["Needs review", "warn"],
    applying: ["Applying", "accent"], restoring: ["Restoring", "accent"], applied: ["Applied", "ok"],
    restored: ["Restored", "ok"], done: ["Done", "ok"], failed: ["Failed", "bad"],
    cancelled: ["Cancelled", "neutral"], rejected: ["Rejected", "neutral"],
  };
  const ACTIVE = new Set(["queued", "running", "applying", "restoring"]);
  const $ = (id) => document.getElementById(id);
  const list = $("jobs"), statusEl = $("status"), flashEl = $("flash"), summary = $("summary"), closeBtn = $("close");
  const embedded = window.parent !== window;
  let visible = !embedded, busy = false, timer = null, flashTimer = null, seq = 0, lastSig = null;
  let expanded = new Set(), lastJobs = [];
  // Rebuilt on every render: what the user may be reading or focused on, keyed so it can be put back.
  const planBoxes = new Map(), focusables = new Map();

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = String(text);
    return n;
  }

  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  // Paths come from the model; a newline in one must not draw a fake line in the plan box.
  const clean = (s) => String(s).replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/g, " ");

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
    clearTimeout(flashTimer);
    flashEl.textContent = text;
    flashEl.hidden = !text;
    if (text) flashTimer = setTimeout(() => { flashEl.hidden = true; }, 8000);
  }

  function minutes(job) {
    const end = ACTIVE.has(job.state) ? Date.now() / 1000 : job.updated;
    const m = Math.max(0, Math.round((end - job.created) / 60));
    return m < 1 ? "under 1 min" : m + " min";
  }

  function describeOp(op, i) {
    const n = String(i + 1).padStart(3);
    if (op && op.op === "move") return `${n} move    ${clean(op.src)}\n          → ${clean(op.dst)}`;
    if (op && op.op === "remove") return `${n} remove  ${clean(op.path)}   (to trash, restorable)`;
    if (op && op.op === "mkdir") return `${n} mkdir   ${clean(op.path)}`;
    return `${n} ?       ${clean(JSON.stringify(op))}`;
  }

  function button(text, key, onClick) {
    const b = el("button", "", text);
    b.type = "button";
    b.disabled = busy;
    b.addEventListener("click", onClick);
    focusables.set(key, b);
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
      lastSig = null;  // the busy render disabled the buttons; the next poll must draw them again
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
    const title = job.title || job.kind;
    if (finished && !expanded.has(job.id)) {
      const c = el("button", "card compact");
      c.type = "button";
      c.title = "Show details";
      c.setAttribute("aria-expanded", "false");
      c.append(el("span", "kind"), el("span", "title", title), el("span", "chip " + tone, label));
      c.addEventListener("click", () => { expanded.add(job.id); render(lastJobs); });
      focusables.set(`${job.id}:expand`, c);
      return c;
    }
    const c = el("article", "card" + (job.state === "awaiting_approval" ? " review" : ""));
    const head = el("div", "head");
    head.append(el("span", "kind"), el("span", "title", title), el("span", "chip " + tone, label));
    c.append(head);
    if (job.steps && job.steps.length) c.append(stepsList(job));
    if (ACTIVE.has(job.state) || job.step_count) {
      c.append(el("div", "meta", `${job.step_count} of ${MAX_CALLS} ${job.step_count === 1 ? "step" : "steps"} · ${minutes(job)}`));
    }
    if (job.note) c.append(el("div", "note", job.note));
    if (job.state === "awaiting_approval") {
      const a = el("div", "actions");
      if (Array.isArray(job.plan)) {
        const box = el("div", "plan", job.plan.map(describeOp).join("\n"));
        planBoxes.set(job.id, box);
        c.append(box);
        // Fixed-rule warnings from the server: shown whatever the model's review says.
        (Array.isArray(job.warnings) ? job.warnings : []).forEach((w) => c.append(el("div", "warning", w)));
        if (job.review) c.append(el("div", "review-text", "Review: " + job.review.trim()));
        a.append(button(`Approve ${plural(job.plan.length, "change", "changes")}`, `${job.id}:apply`, () => act(`jobs/${job.id}/apply`)));
      } else {
        c.append(el("div", "note", "The plan file couldn't be read, so it can't be approved here."));
        if (job.review) c.append(el("div", "review-text", "Review: " + job.review.trim()));
      }
      a.append(button("Reject", `${job.id}:reject`, () => act(`jobs/${job.id}/reject`)));
      c.append(a);
    }
    if (job.can_restore) {
      const a = el("div", "actions");
      a.append(button("Restore", `${job.id}:restore`, () => act(`jobs/${job.id}/restore`,
        "Put every file this job moved or removed back where it was?")));
      c.append(a);
    }
    if (job.queue_count) {
      const a = el("div", "actions");
      a.append(button(`Queue ${plural(job.queue_count, "follow-up job", "follow-up jobs")}`, `${job.id}:queue`,
        () => act(`jobs/${job.id}/queue`)));
      c.append(a);
    }
    return c;
  }

  function postCounts(running, review) {
    if (embedded) window.parent.postMessage({type: "agent-jobs-counts", running, review}, location.origin);
  }

  function render(jobs) {
    lastJobs = jobs;
    // A rebuild must not undo what the user is doing: keep the list and plan scroll positions and the focused button.
    const listTop = list.scrollTop;
    const planScroll = new Map();
    planBoxes.forEach((box, id) => planScroll.set(id, [box.scrollTop, box.scrollLeft]));
    let focusKey = null;
    focusables.forEach((node, key) => { if (node === document.activeElement) focusKey = key; });
    planBoxes.clear();
    focusables.clear();
    list.replaceChildren();
    if (!jobs.length) {
      list.append(el("div", "empty", "No agent jobs yet. Ask the chat to research, plan or organize something."));
    } else {
      jobs.forEach((j) => list.append(card(j)));
    }
    list.scrollTop = listTop;
    planBoxes.forEach((box, id) => {
      const s = planScroll.get(id);
      if (s) { box.scrollTop = s[0]; box.scrollLeft = s[1]; }
    });
    const again = focusKey && focusables.get(focusKey);
    if (again && !again.disabled) again.focus();
    const running = jobs.filter((j) => ACTIVE.has(j.state)).length;
    const review = jobs.filter((j) => j.state === "awaiting_approval").length;
    summary.textContent = [running && `${running} running`, review && `${review} to review`].filter(Boolean).join(" · ");
    $("empty-trash").disabled = busy;
    postCounts(running, review);
  }

  // What the screen shows, so an unchanged poll doesn't rebuild it. Elapsed minutes of live jobs are part of it.
  const signature = (jobs) => JSON.stringify(jobs) + "|" + jobs.filter((j) => ACTIVE.has(j.state)).map(minutes).join(",");

  async function refresh() {
    clearTimeout(timer);
    const mine = ++seq;
    let jobs = null, failure = null;
    try {
      const data = await api("GET", "jobs");
      jobs = Array.isArray(data.jobs) ? data.jobs : [];
    } catch (e) { failure = e; }
    if (mine !== seq) return;  // a newer poll (or the refresh after an action) owns the screen and the timer
    try {
      if (failure) {
        const e = failure;
        showStatus(e.kind === "no-key" ? "Paste your API key in the Web UI under Settings → General, then reopen this panel."
          : e.kind === "down" ? "Agents server isn't running. Start the servers from the launcher."
          : e.kind === "forbidden" ? "The API key was refused. Copy it again from the launcher and paste it in Settings → General."
          : "Couldn't load jobs: " + e.message);
        lastSig = null;
        postCounts(0, 0);  // don't leave a stale number on the Jobs button
      } else {
        showStatus("");
        const sig = signature(jobs);
        if (!busy && sig !== lastSig) {
          lastSig = sig;
          render(jobs);
        }
      }
    } catch (e) {
      // A render bug must not stop the polling below.
      lastSig = null;
      showStatus("Couldn't show jobs: " + e.message);
    }
    timer = setTimeout(refresh, visible ? VISIBLE_MS : HIDDEN_MS);
  }

  if (embedded) {
    closeBtn.hidden = false;
    closeBtn.addEventListener("click", () => window.parent.postMessage({type: "agent-jobs-close"}, location.origin));
  }
  // Emptying the trash is permanent, so it takes two clicks: the first only arms the button for a few seconds.
  // (window.confirm would put the default on OK, so Enter would delete.)
  const trashBtn = $("empty-trash");
  let trashTimer = null, trashArmedAt = 0;
  const disarmTrash = () => {
    clearTimeout(trashTimer);
    trashTimer = null;
    trashBtn.textContent = "Empty trash";
    trashBtn.classList.remove("danger");
  };
  trashBtn.addEventListener("click", () => {
    if (trashTimer === null) {
      trashBtn.textContent = "Confirm empty trash";
      trashBtn.classList.add("danger");
      trashTimer = setTimeout(disarmTrash, TRASH_ARM_MS);
      trashArmedAt = Date.now();
      return;
    }
    if (Date.now() - trashArmedAt < TRASH_CONFIRM_DELAY_MS) return;  // a double-click or a held Enter is not a confirmation
    disarmTrash();
    act("trash/empty");
  });
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
