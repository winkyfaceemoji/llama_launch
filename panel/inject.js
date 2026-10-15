(() => {
  if (window.top !== window || document.getElementById("ajp-root")) return;
  const OPEN_KEY = "agentJobsPanelOpen";
  const css = `
#ajp-root{all:initial}
#ajp-root .ajp-btn{position:fixed;top:10px;right:12px;z-index:2147483000;display:flex;align-items:center;gap:6px;
  font:500 13px/1 system-ui,-apple-system,"Segoe UI",sans-serif;padding:7px 11px;border-radius:8px;cursor:pointer;
  background:var(--ajp-surface);color:var(--ajp-text);border:0.5px solid var(--ajp-border)}
#ajp-root .ajp-badge{min-width:16px;padding:1px 5px;border-radius:8px;font-size:11px;text-align:center;
  background:var(--ajp-warn-bg);color:var(--ajp-warn-text)}
#ajp-root .ajp-badge[hidden]{display:none}
#ajp-root .ajp-frame{position:fixed;top:0;right:0;width:360px;max-width:100vw;height:100vh;z-index:2147482999;
  border:0;border-left:0.5px solid var(--ajp-border);background:var(--ajp-bg);transform:translateX(100%);
  transition:transform .18s ease-out}
#ajp-root.ajp-open .ajp-frame{transform:none}
#ajp-root.ajp-open .ajp-btn{right:372px}
#ajp-root{--ajp-bg:#faf9f5;--ajp-surface:#ffffff;--ajp-text:#1f1e1d;--ajp-border:rgba(31,30,29,.18);
  --ajp-warn-bg:#faeeda;--ajp-warn-text:#633806}
@media (prefers-color-scheme:dark){#ajp-root{--ajp-bg:#262624;--ajp-surface:#30302e;--ajp-text:#faf9f5;
  --ajp-border:rgba(250,249,245,.18);--ajp-warn-bg:#633806;--ajp-warn-text:#faeeda}}`;
  const style = document.createElement("style");
  style.textContent = css;
  const root = document.createElement("div");
  root.id = "ajp-root";
  const btn = document.createElement("button");
  btn.className = "ajp-btn";
  btn.type = "button";
  btn.setAttribute("aria-label", "Agent jobs");
  btn.setAttribute("aria-expanded", "false");
  const label = document.createElement("span");
  label.textContent = "Jobs";
  const badge = document.createElement("span");
  badge.className = "ajp-badge";
  badge.hidden = true;
  btn.append(label, badge);
  const frame = document.createElement("iframe");
  frame.className = "ajp-frame";
  frame.title = "Agent jobs";
  frame.src = "/agent-jobs";
  root.append(btn, frame);
  document.head.append(style);
  document.body.append(root);

  let open = false;
  try { open = localStorage.getItem(OPEN_KEY) === "1"; } catch (e) {}
  const tell = () => {
    try { frame.contentWindow.postMessage({type: "agent-jobs-visible", visible: open}, location.origin); } catch (e) {}
  };
  const apply = () => {
    root.classList.toggle("ajp-open", open);
    btn.setAttribute("aria-expanded", String(open));
    tell();
  };
  btn.addEventListener("click", () => {
    open = !open;
    try { localStorage.setItem(OPEN_KEY, open ? "1" : "0"); } catch (e) {}
    apply();
  });
  frame.addEventListener("load", tell);
  window.addEventListener("message", (e) => {
    if (e.origin !== location.origin || e.source !== frame.contentWindow) return;
    const d = e.data || {};
    if (d.type !== "agent-jobs-counts") return;
    const n = (d.running | 0) + (d.review | 0);
    badge.textContent = String(n);
    badge.hidden = n === 0;
  });
  apply();
})();
