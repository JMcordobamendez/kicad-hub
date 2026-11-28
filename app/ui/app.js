// kicad-hub desktop app: the page. It shows the state the app's core sends (Rust,
// src-tauri/src/main.rs): the project being watched and the last status.json the engine
// wrote (`kicad_hub.py status`). It never runs anything itself: every action is a command.
//
// Outside the app (tests, a browser) a stand-in core can be given as window.KH_MOCK =
// {invoke(name, args) -> Promise, listen(event, handler)}.
"use strict";

const THEMES = {
  green: { name: "Classic green", mask: "#12382b", pad: "#e2a52b" },
  blue: { name: "Deep blue", mask: "#14324a", pad: "#e8893a" },
  purple: { name: "Purple", mask: "#3a1f4d", pad: "#e2a52b" },
  red: { name: "Red", mask: "#5e1a17", pad: "#e2a52b" },
  black: { name: "Matte black", mask: "#0a0a09", pad: "#e2a52b" },
};
const AREA_LABEL = { schematic: "Schematic", board: "Board", sync: "In sync", parts: "Parts", datasheets: "Datasheets" };

const core = (() => {
  const t = window.__TAURI__;
  if (t && t.core && t.event) return { invoke: t.core.invoke, listen: t.event.listen };
  if (window.KH_MOCK) return window.KH_MOCK;
  return null;
})();

let state = null;
let notice = null; // {ok, message, id}: the answer of the last Show in KiCad

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

const CHECK = '<svg role="img" aria-label="OK" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" ' +
  'stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
const EXPAND = '<svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6M20 4l-7 7M10 20H4v-6M4 20l7-7"/></svg>';

// --- pieces ---------------------------------------------------------------------------------

function pad(ind) {
  const what = { ok: "fine", todo: `${ind.count} to tidy`, error: `${ind.count}, with errors`, off: "not checked" }[ind.state];
  const inner = ind.state === "ok" ? CHECK : ind.state === "off" ? "–" : esc(ind.count);
  return `<span class="pad ${esc(ind.state)}" role="img" aria-label="${esc(ind.label)}: ${esc(what)}">${inner}</span>`;
}

function summary(status) {
  const inds = status && status.indicators ? status.indicators
    : Object.entries(AREA_LABEL).map(([id, label]) => ({ id, label, state: "off", count: 0, note: "not checked yet" }));
  return `<div class="summary" aria-label="Check summary">${inds.map((i) => `
    <div class="ind" data-area="${esc(i.id)}">${pad(i)}<span class="ind-label">${esc(i.label)}</span>
      <span class="ind-note">${esc(i.note)}</span></div>`).join("")}</div>`;
}

function themePicker(where) {
  const current = state.theme in THEMES ? state.theme : "green";
  return `<div class="themes"><span class="themes-label">Theme · <strong>${esc(THEMES[current].name)}</strong></span>
    <div class="swatches">${Object.entries(THEMES).map(([key, t]) => `
      <button type="button" class="swatch" data-theme="${key}" aria-label="${esc(t.name)} theme"
        aria-pressed="${key === current}"><span style="background:${t.mask};border-color:${t.pad}"></span></button>`).join("")}
    </div></div>`.replace('class="themes"', `class="themes" data-where="${where}"`);
}

function clock(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
}

function whenLine(status, side) {
  if (state.checking) return status && status.state === "done" ? "Checking again…" : "Checking your project…";
  if (!status) return "";
  const secs = Math.round(status.seconds || 0);
  const at = clock(status.checked_at);
  let text = at ? `Checked at ${at}, in ${secs} ${secs === 1 ? "second" : "seconds"}.` : "";
  if (side) text += " It checks again when you save.";
  return text.trim();
}

function headline(status) {
  if (state.problem) return "kicad-hub can't run its checks.";
  if (!status) return state.checking ? "Checking your project…" : "Not checked yet.";
  return status.headline;
}

function meta(status) {
  const p = state.project || {};
  return [p.name || (status && status.project), status && status.branch, status && status.commit]
    .filter(Boolean).map(esc).join(" · ");
}

function rowsCard(status) {
  const items = (status && status.items) || [];
  const canShow = state.features && state.features.show_in_kicad;
  const anyError = items.some((i) => i.blocking);
  let body;
  if (state.problem || (status && status.state === "failed")) {
    body = `<p class="foot-note">${esc(state.problem ? "The app could not start the engine:" : "What went wrong:")}</p>
      <pre class="problem">${esc(state.problem || status.detail || "")}</pre>
      <p class="foot-note">All the engine's messages are in .kicad-hub/app.log, in the project's folder.</p>`;
  } else if (!status) {
    body = `<p class="empty">The list appears after the first check.</p>`;
  } else if (!items.length) {
    body = `<p class="empty">Nothing to tidy up.</p>`;
  } else {
    body = `<ul class="rows">${items.map((it) => {
      const show = canShow && ((it.select || []).length || (it.footprints || []).length);
      const button = show ? `<button type="button" class="pill soft show" data-id="${esc(it.id)}">Show in KiCad</button>` : "";
      const said = notice && notice.id === it.id
        ? `<span class="said ${notice.ok ? "good" : "bad"}" role="status">${esc(notice.message)}</span>` : "";
      return `<li class="row${it.blocking ? " blocking" : ""}${show ? " can-show" : ""}" data-id="${esc(it.id)}">
        <span class="chip">${esc(it.ref || AREA_LABEL[it.area] || "")}</span>
        <span class="row-text"><span class="row-title">${esc(it.title)}</span>
          ${it.blocking ? '<span class="flag full-only">Fix before ordering</span>' : ""}
          ${it.detail ? `<span class="row-detail">${esc(it.detail)}</span>` : ""}${said}</span>${button}</li>`;
    }).join("")}</ul>`;
    if (canShow) body += `<p class="foot-note side-only">Tap one and KiCad selects it on the board.</p>`;
  }
  const note = !items.length ? "" : anyError ? "Fix the ones marked first: they stop the order."
    : "None of this stops you ordering the board.";
  return `<section class="card" aria-labelledby="tidy"><div class="card-head"><h2 id="tidy">To tidy up</h2>
    ${note ? `<span class="muted">${note}</span>` : ""}</div>${body}</section>`;
}

// --- screens --------------------------------------------------------------------------------

function welcome() {
  return `<div class="welcome"><div class="welcome-card">
    <span class="brand"><span class="brand-pad"></span><span class="brand-name">kicad-hub</span></span>
    <h1>Pick your KiCad project and I'll keep an eye on it.</h1>
    <p>Choose the folder with your project's .kicad_pro file. Each time you save in KiCad, it gets checked
      again, and this window says in plain words what is left before you can order the board.</p>
    <button type="button" class="pill primary" data-act="choose">Choose project folder</button>
    ${state.problem ? `<pre class="problem">${esc(state.problem)}</pre>` : ""}
    ${themePicker("welcome")}
  </div></div>`;
}

function project() {
  const s = state.status;
  return `
  <nav class="nav full-only" aria-label="Sections">
    <span class="brand"><span class="brand-pad"></span><span class="brand-name">kicad-hub</span></span>
    <div class="sections"><button type="button" class="section" aria-current="page">Status</button></div>
    <div class="nav-bottom">
      ${themePicker("nav")}
      <p class="watching"><span class="dot"></span><span>Watching your project folder. Each time you save in KiCad,
        it gets checked again.</span></p>
      <button type="button" class="pill on-mask" data-act="side">Beside KiCad</button>
      <button type="button" class="pill on-mask" data-act="choose">Another project</button>
    </div>
  </nav>
  <main class="main">
    <header class="side-head side-only">
      <div class="side-top"><span class="meta">${meta(s)}</span>
        <button type="button" class="round" data-act="full" aria-label="Open the full window">${EXPAND}</button></div>
      <h1 class="headline">${esc(headline(s))}</h1>
      <span class="when">${esc(whenLine(s, true))}</span>
    </header>
    <header class="head full-only">
      <div class="head-text">
        <p class="meta">${meta(s)}</p>
        <h1 class="headline">${esc(headline(s))}</h1>
        <div class="when"><span class="muted">${esc(whenLine(s, false))}</span>
          <button type="button" class="pill" data-act="check" ${state.checking ? "disabled" : ""}>Check now</button></div>
      </div>
      ${summary(s)}
    </header>
    <div class="side-only">${summary(s)}</div>
    ${rowsCard(s)}
    <div class="side-foot side-only">${themePicker("side")}</div>
  </main>`;
}

function render() {
  const root = document.getElementById("app");
  if (!core) {
    root.innerHTML = `<div class="welcome"><div class="welcome-card"><h1>Open kicad-hub from its app.</h1>
      <p>This page is the inside of the kicad-hub desktop app.</p></div></div>`;
    return;
  }
  if (!state) return;
  document.documentElement.dataset.theme = state.theme in THEMES ? state.theme : "green";
  root.classList.toggle("checking", !!state.checking);
  root.innerHTML = state.project ? project() : welcome();
}

// --- actions --------------------------------------------------------------------------------

async function act(name, args) {
  try {
    const next = await core.invoke(name, args || {});
    if (next && typeof next === "object") { state = next; render(); }
  } catch (e) {
    state = Object.assign({}, state, { problem: String(e && e.message ? e.message : e) });
    render();
  }
}

document.addEventListener("click", (ev) => {
  const el = ev.target.closest("button");
  if (!el || !core) return;
  if (el.dataset.theme) {
    state = Object.assign({}, state, { theme: el.dataset.theme }); // at once, before the core answers
    render();
    act("set_theme", { theme: el.dataset.theme });
  } else if (el.dataset.act === "choose") act("choose_project");
  else if (el.dataset.act === "check") act("check_now");
  else if (el.dataset.act === "side") act("set_mode", { mode: "side" });
  else if (el.dataset.act === "full") act("set_mode", { mode: "full" });
  else if (el.classList.contains("show")) showInKiCad(el.dataset.id);
});

// In the strip the whole row is the button (there is no room for one).
document.addEventListener("click", (ev) => {
  if (ev.target.closest("button") || !core) return;
  const row = ev.target.closest(".row.can-show");
  if (row && window.matchMedia("(max-width: 719px)").matches) showInKiCad(row.dataset.id);
});

async function showInKiCad(id) {
  notice = { ok: true, message: "Asking KiCad…", id };
  render();
  try {
    const r = await core.invoke("show_in_kicad", { id });
    notice = { ok: !!r.ok, message: r.message, id };
  } catch (e) {
    notice = { ok: false, message: String(e && e.message ? e.message : e), id };
  }
  render();
}

(async function start() {
  render();
  if (!core) return;
  await core.listen("state", (event) => { state = event.payload; render(); });
  await act("get_state");
})();
