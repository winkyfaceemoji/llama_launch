(() => {
  if (window.top !== window || document.getElementById("ajp-root")) return;
  const OPEN_KEY = "agentJobsPanelOpen";
  const css = `
#ajp-root{all:initial}
#ajp-root .ajp-btn{position:fixed;top:16px;right:16px;z-index:9;display:flex;align-items:center;justify-content:center;
  width:40px;height:40px;padding:0;border:1px solid var(--ajp-border);border-radius:9999px;cursor:pointer;
  background:var(--ajp-fill);color:var(--ajp-text);box-shadow:0 1px 2px rgba(0,0,0,.08)}
#ajp-root .ajp-btn:hover{background:var(--ajp-hover)}
/* From 768px the Web UI's chat-tab bar (z-10, 53px) spans the top, so the button sits just under it; from
   1024px the message box no longer reaches the right edge, so it moves to the free bottom-right corner. */
@media (min-width:768px){#ajp-root .ajp-btn{top:64px}}
@media (min-width:1024px){#ajp-root .ajp-btn{top:auto;bottom:16px}}
#ajp-root .ajp-btn:focus-visible{outline:2px solid var(--ajp-ring);outline-offset:2px}
#ajp-root .ajp-btn svg{width:18px;height:18px}
#ajp-root .ajp-badge{position:absolute;top:6px;right:6px;box-shadow:0 0 0 2px var(--ajp-page);
  font:600 10px/16px system-ui,-apple-system,"Segoe UI",sans-serif;text-align:center}
#ajp-root .ajp-badge.running{width:8px;height:8px;border-radius:50%;background:var(--ajp-muted)}
#ajp-root .ajp-badge.review{min-width:16px;height:16px;padding:0 4px;border-radius:8px;top:1px;right:1px;
  background:var(--ajp-review-bg);color:var(--ajp-review-text)}
#ajp-root .ajp-badge[hidden]{display:none}
#ajp-root .ajp-frame{position:fixed;top:0;right:0;width:360px;max-width:100vw;height:100vh;z-index:39;
  border:0;border-left:0.5px solid var(--ajp-border);background:var(--ajp-bg);transform:translateX(100%);
  visibility:hidden;transition:transform .18s ease-out,visibility 0s linear .18s}
#ajp-root.ajp-open .ajp-frame{transform:none;visibility:visible;transition:transform .18s ease-out,visibility 0s}
#ajp-root.ajp-open .ajp-btn{right:372px}
#ajp-root.ajp-away .ajp-btn,#ajp-root.ajp-away .ajp-frame{display:none}
@media (max-width:479px){#ajp-root.ajp-open .ajp-btn{display:none}}
#ajp-root{color-scheme:light;--ajp-page:#ffffff;--ajp-bg:#fafafa;--ajp-text:#0a0a0a;--ajp-border:rgba(0,0,0,.15);
  --ajp-fill:#f5f5f5;--ajp-hover:#ebebeb;--ajp-ring:#a1a1a1;--ajp-muted:#737373;--ajp-review-bg:#b45309;--ajp-review-text:#ffffff}
#ajp-root.ajp-dark{color-scheme:dark;--ajp-page:#0d0d0d;--ajp-bg:#141414;--ajp-text:#fafafa;--ajp-border:rgba(255,255,255,.15);
  --ajp-fill:#262626;--ajp-hover:#333333;--ajp-ring:#737373;--ajp-muted:#a1a1a1;--ajp-review-bg:#f59e0b;--ajp-review-text:#0d0d0d}`;
  // A list-with-checks icon (static markup, not model output).
  const ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
    'stroke-linejoin="round" aria-hidden="true"><path d="m3 17 2 2 4-4"/><path d="m3 7 2 2 4-4"/>' +
    '<path d="M13 6h8"/><path d="M13 12h8"/><path d="M13 18h8"/></svg>';
  const style = document.createElement("style");
  style.textContent = css;
  const root = document.createElement("div");
  root.id = "ajp-root";
  const btn = document.createElement("button");
  btn.className = "ajp-btn";
  btn.type = "button";
  btn.title = "Jobs";
  btn.setAttribute("aria-label", "Agent jobs");
  btn.setAttribute("aria-expanded", "false");
  btn.innerHTML = ICON;
  const badge = document.createElement("span");
  badge.className = "ajp-badge";
  badge.hidden = true;
  btn.append(badge);
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
    // A plan waiting for approval is the one state that must catch the eye: an amber count.
    // Running jobs only get a quiet dot.
    const running = d.running | 0, review = d.review | 0, n = running + review;
    badge.className = "ajp-badge " + (review ? "review" : "running");
    badge.textContent = review ? String(review) : "";
    badge.hidden = n === 0;
    const parts = [review && `${review} ${review === 1 ? "plan" : "plans"} to review`, running && `${running} running`];
    btn.setAttribute("aria-label", ["Agent jobs", ...parts.filter(Boolean)].join(", "));
  });
  apply();
})();
