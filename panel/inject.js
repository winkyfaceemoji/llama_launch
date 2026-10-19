(() => {
  if (window.top !== window || document.getElementById("ajp-root")) return;
  const OPEN_KEY = "agentJobsPanelOpen";
  const css = `
#ajp-root{all:initial}
#ajp-root .ajp-btn{position:fixed;top:10px;right:12px;z-index:40;display:flex;align-items:center;gap:6px;
  font:500 13px/1 system-ui,-apple-system,"Segoe UI",sans-serif;padding:7px 11px;border-radius:8px;cursor:pointer;
  background:var(--ajp-surface);color:var(--ajp-text);border:0.5px solid var(--ajp-border)}
#ajp-root .ajp-badge{min-width:16px;padding:1px 5px;border-radius:8px;font-size:11px;text-align:center;
  background:var(--ajp-warn-bg);color:var(--ajp-warn-text)}
#ajp-root .ajp-badge[hidden]{display:none}
#ajp-root .ajp-frame{position:fixed;top:0;right:0;width:360px;max-width:100vw;height:100vh;z-index:39;
  border:0;border-left:0.5px solid var(--ajp-border);background:var(--ajp-bg);transform:translateX(100%);
  visibility:hidden;transition:transform .18s ease-out,visibility 0s linear .18s}
#ajp-root.ajp-open .ajp-frame{transform:none;visibility:visible;transition:transform .18s ease-out,visibility 0s}
#ajp-root.ajp-open .ajp-btn{right:372px}
#ajp-root.ajp-away .ajp-btn,#ajp-root.ajp-away .ajp-frame{display:none}
@media (max-width:479px){#ajp-root.ajp-open .ajp-btn{display:none}}
#ajp-root{color-scheme:light;--ajp-bg:#fafafa;--ajp-surface:#ffffff;--ajp-text:#0a0a0a;--ajp-border:rgba(0,0,0,.15);
  --ajp-warn-bg:#fef3c7;--ajp-warn-text:#78350f}
#ajp-root.ajp-dark{color-scheme:dark;--ajp-bg:#141414;--ajp-surface:#1c1c1c;--ajp-text:#fafafa;
  --ajp-border:rgba(255,255,255,.15);--ajp-warn-bg:#451a03;--ajp-warn-text:#fde68a}`;
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
  // The Web UI's Settings pages are routed with the hash; the panel would sit on top of them, so it steps aside.
  // This never touches the saved open state, so leaving Settings brings back exactly what was there.
  const onSettings = () => location.hash.startsWith("#/settings");
  const tell = () => {
    try { frame.contentWindow.postMessage({type: "agent-jobs-visible", visible: open && !onSettings()}, location.origin); } catch (e) {}
  };
  const apply = () => {
    const away = onSettings(), shown = open && !away;
    root.classList.toggle("ajp-open", open);
    root.classList.toggle("ajp-away", away);
    btn.setAttribute("aria-expanded", String(open));
    // A closed panel is only slid off-screen, so keep it out of the tab order and the accessibility tree.
    frame.inert = !shown;
    if (shown) frame.removeAttribute("aria-hidden"); else frame.setAttribute("aria-hidden", "true");
    tell();
  };
  const setOpen = (value) => {
    open = value;
    try { localStorage.setItem(OPEN_KEY, open ? "1" : "0"); } catch (e) {}
    apply();
  };
  btn.addEventListener("click", () => setOpen(!open));
  // The Web UI navigates with history.pushState/replaceState, which fire no event, so also watch the address.
  // (Patching history instead would break the Web UI's router if it ever compares the functions.)
  let lastHref = location.href;
  const routeChanged = () => {
    if (location.href === lastHref) return;
    lastHref = location.href;
    apply();
  };
  window.addEventListener("hashchange", routeChanged);
  window.addEventListener("popstate", routeChanged);
  setInterval(routeChanged, 300);
  frame.addEventListener("load", tell);
  // Follow the Web UI's own theme (System / Light / Dark), which it marks with a "dark" class on <html>.
  const syncTheme = () => {
    const dark = document.documentElement.classList.contains("dark");
    root.classList.toggle("ajp-dark", dark);
    try { frame.contentWindow.postMessage({type: "agent-jobs-theme", dark}, location.origin); } catch (e) {}
  };
  new MutationObserver(syncTheme).observe(document.documentElement, {attributes: true, attributeFilter: ["class"]});
  frame.addEventListener("load", syncTheme);
  syncTheme();
  window.addEventListener("message", (e) => {
    if (e.origin !== location.origin || e.source !== frame.contentWindow) return;
    const d = e.data || {};
    if (d.type === "agent-jobs-close") {
      setOpen(false);
      btn.focus();
      return;
    }
    if (d.type !== "agent-jobs-counts") return;
    const n = (d.running | 0) + (d.review | 0);
    badge.textContent = String(n);
    badge.hidden = n === 0;
    btn.setAttribute("aria-label", n ? `Agent jobs, ${n} ${n === 1 ? "needs" : "need"} attention` : "Agent jobs");
  });
  apply();
})();
