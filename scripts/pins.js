/* kicad-hub: comments pinned on the board (phase 5 prototype).
 *
 * Loaded by scripts/portal.html on pull request pages only. It adds, to the PCB and
 * schematic tabs:
 *   - a "Comment" mode: click a part (or any point), write a note, post it as a pull
 *     request comment through the GitHub REST API (or copy it, when there is no token);
 *   - a "Comments" card in the side panel listing the pins found in the PR's comments;
 *   - numbered markers drawn over the viewer, kept in place while panning and zooming.
 *
 * A pin is a normal PR conversation comment ending in a hidden marker:
 *   <!-- kicad-hub-pin {"ref":"U12","x":23.4,"y":41.1,"layer":"F.Cu","page":"pcb","sha":"..."} -->
 * so it also reads fine on GitHub, and anyone can make one by hand.
 *
 * The viewer is KiCanvas, reached through the objects portal.html finds in its shadow
 * DOM (viewer.viewport.camera: screen_to_world / world_to_screen, canvas-relative CSS
 * pixels, world units = mm in the file's coordinates, y down).
 *
 * All comment text is user content: it is only ever inserted escaped, as plain text.
 * The token lives in localStorage and is sent to api.github.com only.
 */
(function () {
  "use strict";

  const API = "https://api.github.com";
  const TOKEN_KEY = "kicad-hub-token";
  const MARK = "kicad-hub-pin";
  const PIN = "\u{1F4CD}", DASH = "\u2014";

  let D = null, ctx = null;
  // state shared by the PCB and schematic views
  const S = {comments: [], state: "loading", error: "", tokenBad: false, views: {}, mode: null,
             pending: null, active: null, deep: null, started: false};

  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c =>
    ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const r2 = n => Math.round(n * 100) / 100;
  const enc = encodeURIComponent;
  const pageOf = kind => kind === "pcb" ? "pcb" : "schematic";   // marker "page" = portal tab id
  const kindOf = page => page === "pcb" ? "pcb" : "sch";

  /* ---------- token (never logged, never put in a URL) ---------- */
  let memToken = "";
  const token = {
    get() { try { return localStorage.getItem(TOKEN_KEY) || memToken; } catch (e) { return memToken; } },
    set(v) { memToken = v; try { localStorage.setItem(TOKEN_KEY, v); } catch (e) { /* kept for this visit only */ } },
    clear() { memToken = ""; try { localStorage.removeItem(TOKEN_KEY); } catch (e) { /* nothing stored */ } },
  };

  /* ---------- GitHub API ---------- */
  function gh(path, opts) {
    opts = opts || {};
    const t = opts.anonymous ? "" : token.get();
    const headers = {Accept: "application/vnd.github+json"};
    if (t) headers.Authorization = "Bearer " + t;
    if (opts.body) headers["Content-Type"] = "application/json";
    return fetch(API + path, {method: opts.method || "GET", headers: headers, body: opts.body, cache: "no-store"});
  }
  const commentsPath = () => {
    const [owner, name] = D.repo.split("/");
    return `/repos/${enc(owner)}/${enc(name)}/issues/${D.pr}/comments`;
  };

  /* ---------- the comment <-> pin format ---------- */
  const safeRef = s => String(s || "").replace(/[^\w#.+\-\/~]/g, "").slice(0, 40);
  const safeLabel = s => String(s || "").replace(/[*_`\[\]()<>|\\]/g, "").slice(0, 60);
  const sheetName = f => safeLabel(String(f || "").split("/").pop().replace(/\.kicad_sch$/, ""));

  function marker(p) {
    const o = {};
    if (p.ref) o.ref = p.ref;
    o.x = p.x; o.y = p.y;
    if (p.layer) o.layer = p.layer;
    o.page = p.page;
    if (p.sheet) o.sheet = p.sheet;
    o.sha = D.sha;
    // "<" and ">" escaped so nothing in the data can close the HTML comment
    return `<!-- ${MARK} ${JSON.stringify(o).replace(/</g, "\\u003c").replace(/>/g, "\\u003e")} -->`;
  }
  function link(p) {
    const q = new URLSearchParams({at: p.x + "," + p.y});
    if (p.ref) q.set("ref", p.ref);
    if (p.layer) q.set("layer", p.layer);
    if (p.sheet) q.set("sheet", p.sheet);
    return `${location.origin}${location.pathname}?${q}#${p.page}`;
  }
  function format(p, text) {
    const where = p.layer || (p.sheet ? "sheet " + sheetName(p.sheet) : "");
    const head = `${PIN} **${p.ref || "point"}** (x ${p.x.toFixed(2)}, y ${p.y.toFixed(2)} mm${where ? ", " + where : ""})`;
    // a stray "<!--" in the text would hide the rest of the comment, marker included
    const body = text.trim().replace(/<!--/g, "&lt;!--");
    return `${head} ${DASH} ${body}\n\n[Open on the board](${link(p)})\n${marker(p)}`;
  }

  function parse(c) {
    const m = /<!--\s*kicad-hub-pin\s+(\{[\s\S]*?\})\s*-->/.exec(c.body || "");
    if (!m) return null;
    let p;
    try { p = JSON.parse(m[1]); } catch (e) { return null; }
    if (!p || typeof p !== "object") return null;
    const x = Number(p.x), y = Number(p.y);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    const str = (v, n) => typeof v === "string" ? v.slice(0, n) : "";
    const url = (u, re) => typeof u === "string" && re.test(u) ? u : "";
    let text = String(c.body).replace(m[0], "").replace(/^\[Open on the board[^\n]*$/gm, "").trim();
    text = text.replace(/^\u{1F4CD}[^\n]*?\)\s+\u2014\s*/u, "").trim();
    const u = c.user || {};
    return {id: c.id, ref: safeRef(p.ref), x: x, y: y, layer: str(p.layer, 40), page: p.page === "pcb" ? "pcb" : "schematic",
            sheet: str(p.sheet, 200), sha: str(p.sha, 40), text: text, date: c.created_at,
            login: str(u.login, 60), avatar: url(u.avatar_url, /^https:\/\/avatars\.githubusercontent\.com\//),
            url: url(c.html_url, /^https:\/\/github\.com\//)};
  }

  // back from the GitHub tab after pasting: look for the new comment
  window.addEventListener("focus", () => { if (S.awaiting && S.state !== "loading") load(); });

  async function load() {
    S.state = "loading"; S.error = ""; renderAll();
    try {
      const all = [];
      for (let page = 1; page <= 10; page++) {
        let r = await gh(`${commentsPath()}?per_page=100&page=${page}`);
        if (r.status === 401 && token.get()) {          // an expired token must not break reading
          S.tokenBad = true;
          r = await gh(`${commentsPath()}?per_page=100&page=${page}`, {anonymous: true});
        }
        if (!r.ok) throw new Error(r.status === 403 || r.status === 429
          ? "GitHub's rate limit for anonymous requests was reached (60 per hour). Set a token below or try later."
          : `GitHub answered ${r.status}.`);
        const list = await r.json();
        all.push(...list);
        if (list.length < 100) break;
      }
      S.comments = all.map(parse).filter(Boolean);
      S.state = "ok";
      applyDeep();
      // the comment pasted on GitHub came back: close the form and show it
      const w = S.awaiting, hit = w && S.comments.find(c => !w.known.has(c.id) && Math.abs(c.x - w.x) < 0.006 && Math.abs(c.y - w.y) < 0.006);
      if (hit) { S.awaiting = null; S.active = hit.id; cancel(); }
    } catch (e) {
      S.state = "error"; S.error = (e && e.message) || "Could not load the comments.";
    }
    renderAll();
  }

  // arrived from a pin's link: mark that pin as the active one (once both the link and the comments are known)
  function applyDeep() {
    if (!S.deep || S.state !== "ok") return;
    const d = S.deep, hit = S.comments.find(c => c.ref === d.ref && Math.abs(c.x - d.x) < 0.006 && Math.abs(c.y - d.y) < 0.006);
    if (hit) S.active = hit.id;
    S.deep = null;
  }

  /* ---------- KiCanvas helpers ---------- */
  const camOf = v => v && v.viewport && v.viewport.camera;
  const sideLayer = v => { const c = camOf(v); return c && c.flipped ? "B.Cu" : "F.Cu"; };
  const activeSheet = view => { const p = view.project && view.project.active_page; return p ? String(p.filename || "") : ""; };
  const forKind = kind => S.comments.filter(c => c.page === pageOf(kind));

  function zoomPoint(v, x, y, w) {
    const cam = camOf(v); if (!cam) return;
    cam.bbox = {x: x - w / 2, y: y - w / 2, w: w, h: w};   // the setter only reads x, y, w, h
    v.draw();
  }
  // select the part (or zoom to the point) and, on the PCB, turn the board to the pin's side
  function goTo(view, t, delay) {
    const v = view.v;
    try {
      if (view.kind === "pcb" && /^[FB]\./.test(t.layer || "")) {
        const bottom = /^B\./.test(t.layer), cam = camOf(v);
        if (cam && bottom !== !!cam.flipped) { const b = document.querySelector(`[data-side="${bottom ? "bottom" : "top"}"]`); if (b) b.click(); }
      }
      let wait = delay || 0;
      if (view.project && t.sheet && activeSheet(view) !== t.sheet) {
        const page = [...view.project.pages()].find(p => String(p.filename || "") === t.sheet);
        if (page) { view.project.set_active_page(page); wait += 450; }   // the sheet loads asynchronously
      }
      setTimeout(() => {
        try {
          let found = false;
          if (t.ref) { v.select(t.ref); found = !!v.selected; }
          if (found) v.zoom_to_selection(); else zoomPoint(v, t.x, t.y, view.kind === "pcb" ? 20 : 40);
          v.draw();
        } catch (e) { /* the viewer is still loading */ }
      }, wait);
    } catch (e) { /* ignore */ }
  }

  /* ---------- attaching to a viewer ---------- */
  function attach(kind, v) {
    if (S.views[kind] || !D) return;
    const embed = document.getElementById("kc-" + kind);
    const host = embed && embed.closest(".viewer");
    const sel = document.getElementById("sel-" + kind);
    const bar = host && host.querySelector(".vbar");
    if (!host || !sel || !bar || !v.canvas) return;

    const view = {kind: kind, v: v, embed: embed, host: host, pins: [], key: null, lastSel: null, down: null,
                  project: kind === "sch" ? ctx.findProject(embed.shadowRoot || embed) : null};
    S.views[kind] = view;

    view.btn = document.createElement("button");
    view.btn.className = "btn"; view.btn.type = "button"; view.btn.textContent = "Comment";
    view.btn.title = "Pin a comment on a part or a point of the design";
    bar.insertBefore(view.btn, bar.querySelector(".hint"));

    view.layer = document.createElement("div");
    view.layer.className = "kh-layer";
    host.appendChild(view.layer);

    let layers = ["F.Cu", "B.Cu", "F.Silkscreen", "B.Silkscreen", "Edge.Cuts"];
    if (kind === "pcb") {
      try {
        const names = [...v.layers.in_ui_order()].map(l => l.name.replace(/\.SilkS$/, ".Silkscreen")
          .replace(/\.CrtYd$/, ".Courtyard").replace(/\.Adhes$/, ".Adhesive")).filter(n => /^[\w.]+$/.test(n));
        if (names.length) layers = names;
      } catch (e) { /* keep the short list */ }
    }
    view.card = document.createElement("div");
    view.card.className = "card side kh-card";
    view.card.innerHTML = `<h3>Comments <span class="kh-count"></span></h3><div class="kh-status"></div>
      <form class="kh-form" hidden autocomplete="off">
        <div class="kh-hint"></div>
        <div class="kh-row"><label>${kind === "pcb" ? "Part" : "Symbol"}<input name="ref" maxlength="40" placeholder="none (free point)"></label>
          ${kind === "pcb" ? `<label>Layer<input name="layer" maxlength="40" list="kh-layers"></label>
            <datalist id="kh-layers">${layers.map(n => `<option value="${esc(n)}">`).join("")}</datalist>` : ""}</div>
        <div class="kh-row"><label>X (mm)<input name="x" type="number" step="0.01" required></label>
          <label>Y (mm)<input name="y" type="number" step="0.01" required></label></div>
        <textarea name="text" rows="3" maxlength="2000" required placeholder="What should the author look at?" aria-label="Comment"></textarea>
        <div class="kh-actions"><button class="btn on" type="submit" data-kh="primary"></button>
          <button class="btn" type="button" data-kh="copy">Copy</button><button class="btn" type="button" data-kh="cancel">Cancel</button></div>
        <div class="kh-msg" role="status"></div>
      </form>
      <div class="kh-list"></div><div class="kh-tok"></div>
      <div class="kh-foot"><button class="btn" type="button" data-kh="refresh">Refresh</button>
        <a href="${esc(D.server + "/" + D.repo + "/pull/" + D.pr)}" target="_blank" rel="noopener">Pull request on GitHub</a></div>`;
    sel.insertAdjacentElement("afterend", view.card);
    view.form = view.card.querySelector(".kh-form");

    view.btn.addEventListener("click", () => S.mode === kind ? cancel() : openForm(view));
    view.card.addEventListener("click", e => onCardClick(view, e));
    view.card.addEventListener("keydown", e => {
      const it = e.target.closest && e.target.closest(".kh-item");
      if (it && (e.key === "Enter" || e.key === " ") && e.target === it) { e.preventDefault(); activate(+it.dataset.id, true); }
    });
    view.card.addEventListener("submit", e => { e.preventDefault(); if (e.target === view.form) submit(view); else if (e.target.dataset.kh === "token") saveToken(view, e.target); });
    view.form.addEventListener("input", e => { if (e.target.name === "x" || e.target.name === "y") previewPending(view); });

    v.addEventListener("kicanvas:select", e => {
      const item = e.detail && e.detail.item;
      view.lastSel = {t: performance.now(), item: item};
      // KiCanvas may select after our click handler ran: a part picked right after the
      // click that placed a free point takes it over
      const c = view.click;
      if (S.mode === kind && c && performance.now() - c.t < 500 && item && item.reference && item.reference !== "?") {
        view.click = null;
        setTarget(view, fromItem(view, item, c.w));
      }
    });
    // the click that places a pin: KiCanvas has already handled it (selection, pick)
    v.canvas.addEventListener("pointerdown", e => { view.down = {x: e.clientX, y: e.clientY}; }, true);
    v.canvas.addEventListener("click", e => {
      if (S.mode !== kind) return;
      if (view.down && Math.hypot(e.clientX - view.down.x, e.clientY - view.down.y) > 5) return;  // it was a pan
      const r = v.canvas.getBoundingClientRect(), cam = camOf(v);
      if (!cam) return;
      const w = cam.screen_to_world({x: e.clientX - r.left, y: e.clientY - r.top});
      setTimeout(() => {
        const s = view.lastSel, item = s && performance.now() - s.t < 300 ? s.item : null;
        if (item && item.reference && item.reference !== "?") { view.click = null; setTarget(view, fromItem(view, item, w)); }
        else { view.click = {t: performance.now(), w: w}; setTarget(view, {ref: "", x: w.x, y: w.y}); }
      }, 0);
    }, true);
    if (view.project) view.project.addEventListener("change", () => { view.key = null; renderPins(view); });

    renderCard(view); renderPins(view);
    // a link made by "Open on the board" (?at=x,y&ref=…#pcb) lands on that spot
    const q = new URLSearchParams(location.search), at = (q.get("at") || "").split(",").map(Number);
    if (location.hash.slice(1).split("&")[0] === pageOf(kind) && at.length === 2 && at.every(Number.isFinite)) {
      S.deep = {ref: safeRef(q.get("ref")), x: at[0], y: at[1]};
      goTo(view, {ref: S.deep.ref, x: at[0], y: at[1], layer: q.get("layer") || "", sheet: q.get("sheet") || ""}, 700);  // after the viewer's own zoom_to_board
      applyDeep(); renderAll();
    }
  }

  function fromItem(view, item, w) {
    const pos = item.at && item.at.position;
    return {ref: safeRef(item.reference), x: pos ? pos.x : w.x, y: pos ? pos.y : w.y,
            layer: view.kind === "pcb" && typeof item.layer === "string" ? item.layer : undefined};
  }

  /* ---------- the comment form ---------- */
  function openForm(view) {
    cancel();
    S.mode = view.kind;
    document.dispatchEvent(new CustomEvent("kh:mode", {detail: {mode: "comment", kind: view.kind}}));  // viewer-tools.js leaves Measure
    view.btn.classList.add("on");
    const v = view.v, cam = camOf(v), ctxItem = v.selected && v.selected.context;
    const w = cam ? cam.center : {x: 0, y: 0};
    setTarget(view, ctxItem && ctxItem.reference && ctxItem.reference !== "?" ? fromItem(view, ctxItem, w) : {ref: "", x: w.x, y: w.y});
    view.form.hidden = false;
    try { v.canvas.style.cursor = "crosshair"; } catch (e) { /* cosmetic */ }
    syncForm(view);
    view.form.elements.text.focus();
  }
  function setTarget(view, t) {
    const f = view.form.elements;
    f.ref.value = t.ref || ""; f.x.value = r2(t.x); f.y.value = r2(t.y);
    if (f.layer) f.layer.value = t.layer || sideLayer(view.v);
    view.form.querySelector(".kh-hint").textContent = "Click a part or any point of the board to move the pin, then write the comment.";
    previewPending(view);
  }
  function syncForm(view) {
    const t = !!token.get();
    view.form.querySelector('[data-kh="primary"]').textContent = t ? "Post to the pull request" : "Copy and open the pull request";
    view.form.querySelector('[data-kh="copy"]').hidden = !t;
    if (!view.form.querySelector(".kh-msg").textContent && !t)
      view.form.querySelector(".kh-msg").textContent = "No token set: the comment is copied so you can paste it in the pull request. Set a token below to post from here.";
  }
  function previewPending(view) {
    const f = view.form.elements, x = parseFloat(f.x.value), y = parseFloat(f.y.value);
    S.pending = Number.isFinite(x) && Number.isFinite(y) ? {x: x, y: y, kind: view.kind} : null;
    view.key = null; renderPins(view);
  }
  function cancel() {
    const was = S.mode && S.views[S.mode];
    S.mode = null; S.pending = null; S.awaiting = null;
    for (const view of Object.values(S.views)) {
      view.btn.classList.remove("on"); view.form.hidden = true;
      view.form.querySelector(".kh-msg").textContent = ""; view.form.elements.text.value = "";
      try { view.v.canvas.style.cursor = ""; } catch (e) { /* cosmetic */ }
      view.key = null; renderPins(view);
    }
    return was;
  }
  function read(view) {
    const f = view.form.elements, x = parseFloat(f.x.value), y = parseFloat(f.y.value), text = f.text.value.trim();
    const msg = view.form.querySelector(".kh-msg");
    if (!Number.isFinite(x) || !Number.isFinite(y)) { msg.textContent = "X and Y must be numbers."; return null; }
    if (!text) { msg.textContent = "Write the comment first."; f.text.focus(); return null; }
    const p = {ref: safeRef(f.ref.value), x: r2(x), y: r2(y), page: pageOf(view.kind)};
    if (view.kind === "pcb") p.layer = safeLabel(f.layer && f.layer.value);
    else if (activeSheet(view)) p.sheet = activeSheet(view);
    return {p: p, text: text};
  }
  async function submit(view) {
    const r = read(view); if (!r) return;
    const msg = view.form.querySelector(".kh-msg"), text = format(r.p, r.text);
    if (!token.get()) { S.awaiting = {x: r.p.x, y: r.p.y, known: new Set(S.comments.map(c => c.id))}; await copyAndOpen(view, text); return; }
    const btn = view.form.querySelector('[data-kh="primary"]');
    btn.disabled = true; msg.textContent = "Posting…";
    try {
      const res = await gh(commentsPath(), {method: "POST", body: JSON.stringify({body: text})});
      if (res.status === 401) msg.textContent = "GitHub rejected the token (expired or revoked). Forget it and make a new one.";
      else if (res.status === 403 || res.status === 404)
        msg.textContent = "This token can't comment on this repository. It needs \"Pull requests: Read and write\" on it (see the steps in the README).";
      else if (!res.ok) msg.textContent = `GitHub answered ${res.status}.`;
      else {
        const pin = parse(await res.json());
        if (pin) { S.comments.push(pin); S.active = pin.id; }
        cancel(); renderAll();
        return;
      }
    } catch (e) { msg.textContent = "Could not reach GitHub. You can still copy the comment."; }
    btn.disabled = false;
  }
  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch (e) { /* try the old way */ }
    try {
      const ta = document.createElement("textarea");
      ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      const ok = document.execCommand("copy"); ta.remove(); return ok;
    } catch (e) { return false; }
  }
  async function copyAndOpen(view, text) {
    const msg = view.form.querySelector(".kh-msg");
    const copied = copyText(text);   // started first: the clipboard needs the page focused
    window.open(`${D.server}/${D.repo}/pull/${D.pr}#issuecomment-new`, "_blank", "noopener");
    msg.textContent = (await copied) ? "Copied. Paste it in the pull request's comment box and press Comment, then Refresh here."
      : "Could not copy automatically. Select this text, copy it and paste it in the pull request:";
    if (!(await copied)) {
      const ta = document.createElement("textarea");
      ta.readOnly = true; ta.value = text; ta.rows = 5; ta.className = "kh-manual";
      msg.appendChild(ta); ta.select();
    }
  }

  /* ---------- token box ---------- */
  function renderToken(view) {
    const has = !!token.get();
    view.card.querySelector(".kh-tok").innerHTML = `<details class="kh-token"><summary>GitHub token: <b>${has ? "set" : "not set"}</b></summary>
      <p>To post from this page, make a <b>fine-grained personal access token</b> for this repository only, with
      <b>Pull requests: Read and write</b> (steps in the README). It is saved in this browser only and sent only to
      api.github.com. Comments appear under your GitHub account. Choose a short expiry.</p>
      ${has ? `<button class="btn" type="button" data-kh="forget">Forget token</button>`
        : `<form data-kh="token"><input name="t" type="password" autocomplete="off" spellcheck="false" placeholder="github_pat_…" aria-label="Token">
          <button class="btn" type="submit">Save</button></form>`}<div class="kh-msg" data-kh="tokmsg"></div></details>`;
  }
  function saveToken(view, form) {
    const t = form.elements.t.value.trim(), msg = form.parentElement.querySelector('[data-kh="tokmsg"]');
    if (!/^[\w-]{20,255}$/.test(t)) { msg.textContent = "That doesn't look like a GitHub token."; return; }
    token.set(t); S.tokenBad = false;
    renderAll(); load();
  }

  /* ---------- rendering ---------- */
  function renderAll() { for (const view of Object.values(S.views)) { renderCard(view); renderPins(view); } }

  function renderCard(view) {
    const list = forKind(view.kind), others = S.comments.length - list.length;
    view.card.querySelector(".kh-count").textContent = S.state === "ok" ? `(${list.length})` : "";
    const st = view.card.querySelector(".kh-status");
    st.innerHTML = S.state === "loading" ? "Loading…" : S.state === "error" ? `<span class="kh-err">${esc(S.error)}</span>`
      : S.tokenBad ? `<span class="kh-err">The saved token was rejected, reading without it. Forget it and make a new one.</span>` : "";
    view.card.querySelector(".kh-list").innerHTML = list.map((c, i) => item(c, i + 1)).join("") ||
      (S.state === "ok" ? `<div class="muted">No comments pinned here yet. Press <b>Comment</b> above the ${view.kind === "pcb" ? "board" : "sheet"}.</div>` : "");
    if (S.state === "ok" && others)
      view.card.querySelector(".kh-list").insertAdjacentHTML("beforeend", `<div class="muted kh-other">${others} more on the ${view.kind === "pcb" ? "schematic" : "PCB"} tab.</div>`);
    renderToken(view); syncForm(view);
  }
  function item(c, n) {
    const old = c.sha && D.sha && c.sha.slice(0, 7) !== D.sha.slice(0, 7);
    const when = c.date ? new Date(c.date).toLocaleDateString() : "";
    const where = c.layer || (c.sheet ? sheetName(c.sheet) : "");
    return `<div class="kh-item ${c.id === S.active ? "on" : ""}" data-id="${c.id}" tabindex="0" role="button">
      <span class="kh-num">${n}</span><div class="kh-main">
      <div class="kh-meta">${c.avatar ? `<img src="${esc(c.avatar)}&s=40" alt="" width="18" height="18" referrerpolicy="no-referrer">` : ""}<b>${esc(c.login || "unknown")}</b>
        <span class="muted">${esc(when)}</span></div>
      <div class="kh-where">${c.ref ? `<b>${esc(c.ref)}</b> ` : ""}<span class="muted">x ${c.x.toFixed(2)}, y ${c.y.toFixed(2)}${where ? " · " + esc(where) : ""}${old ? " · older commit " + esc(c.sha.slice(0, 7)) : ""}</span></div>
      <div class="kh-text">${esc(c.text.length > 400 ? c.text.slice(0, 400) + "…" : c.text)}</div>
      ${c.url ? `<a href="${esc(c.url)}" target="_blank" rel="noopener">Open on GitHub</a>` : ""}</div></div>`;
  }

  // markers over the canvas; positions follow the camera (see tick)
  function renderPins(view) {
    view.layer.textContent = "";
    view.pins = [];
    const sheet = activeSheet(view);
    forKind(view.kind).forEach((c, i) => {
      if (view.kind === "sch" && c.sheet && sheet && c.sheet !== sheet) return;
      const el = document.createElement("div");
      el.className = "kh-pin" + (c.id === S.active ? " on" : "");
      el.dataset.id = c.id; el.title = `${c.login}: ${c.text.slice(0, 80)}`;
      el.textContent = String(i + 1);
      if (c.id === S.active) el.appendChild(popover(c));
      view.layer.appendChild(el);
      view.pins.push({x: c.x, y: c.y, el: el, layer: c.layer});
    });
    if (S.pending && S.pending.kind === view.kind) {
      const el = document.createElement("div");
      el.className = "kh-pin pending"; el.textContent = "+";
      view.layer.appendChild(el);
      view.pins.push({x: S.pending.x, y: S.pending.y, el: el, layer: ""});
    }
    view.key = null; position(view);
  }
  function popover(c) {
    const pop = document.createElement("div");
    pop.className = "kh-pop";
    pop.innerHTML = `<button class="kh-x" type="button" aria-label="Close">\u00d7</button>
      <div class="kh-meta">${c.avatar ? `<img src="${esc(c.avatar)}&s=40" alt="" width="18" height="18" referrerpolicy="no-referrer">` : ""}<b>${esc(c.login || "unknown")}</b></div>
      ${c.ref ? `<div><b>${esc(c.ref)}</b></div>` : ""}<div class="kh-text">${esc(c.text.length > 300 ? c.text.slice(0, 300) + "…" : c.text)}</div>
      ${c.url ? `<a href="${esc(c.url)}" target="_blank" rel="noopener">Open on GitHub</a>` : ""}`;
    return pop;
  }
  function position(view, force) {
    const v = view.v, cam = camOf(v);
    if (!cam || !view.embed.offsetParent) return;
    const cr = v.canvas.getBoundingClientRect(), hr = view.host.getBoundingClientRect();
    const key = [cam.center.x, cam.center.y, cam.zoom, cam.flipped, cam.viewport_size.x, cam.viewport_size.y,
                 cr.left - hr.left, cr.top - hr.top, cr.width, cr.height].join();
    if (!force && key === view.key) return;
    view.key = key;
    const L = view.layer.style;
    L.left = (cr.left - hr.left - view.host.clientLeft) + "px"; L.top = (cr.top - hr.top - view.host.clientTop) + "px";
    L.width = cr.width + "px"; L.height = cr.height + "px";
    for (const p of view.pins) {
      const s = cam.world_to_screen({x: p.x, y: p.y});
      p.el.style.left = s.x + "px"; p.el.style.top = s.y + "px";
      // on the PCB, a pin on the other side of the board than the one shown is dimmed
      p.el.classList.toggle("far", view.kind === "pcb" && /^[FB]\./.test(p.layer || "") && /^B\./.test(p.layer) !== !!cam.flipped);
      const pop = p.el.querySelector(".kh-pop");
      if (pop) pop.classList.toggle("left", s.x > cr.width - 270);
    }
  }
  function tick() {
    for (const view of Object.values(S.views)) { try { position(view); } catch (e) { /* viewer reloading */ } }
    requestAnimationFrame(tick);
  }

  function activate(id, navigate) {
    const c = S.comments.find(k => k.id === id); if (!c) return;
    S.active = id;
    const view = S.views[kindOf(c.page)];
    if (navigate && view) goTo(view, c, 0);
    renderAll();
    if (view) {
      const el = view.card.querySelector(`.kh-item[data-id="${c.id}"]`);
      if (el && el.scrollIntoView) el.scrollIntoView({block: "nearest"});
    }
  }
  function onCardClick(view, e) {
    if (e.target.closest("a")) return;   // links open on their own
    const b = e.target.closest("[data-kh]");
    if (b) {
      const k = b.dataset.kh;
      if (k === "cancel") cancel();
      else if (k === "copy") { const r = read(view); if (r) copyText(format(r.p, r.text)).then(ok => { view.form.querySelector(".kh-msg").textContent = ok ? "Copied." : "Could not copy."; }); }
      else if (k === "refresh") load();
      else if (k === "forget") { token.clear(); S.tokenBad = false; renderAll(); load(); }
      return;
    }
    const it = e.target.closest(".kh-item");
    if (it) activate(+it.dataset.id, true);
  }

  function injectStyle() {
    const s = document.createElement("style");
    s.textContent = `
.viewer { position: relative; }
.kh-layer { position: absolute; overflow: hidden; pointer-events: none; z-index: 5; }
.kh-pin { position: absolute; width: 22px; height: 22px; margin: -11px 0 0 -11px; border-radius: 50%; background: var(--brand);
  color: #fff; font: 700 11px/18px system-ui, sans-serif; text-align: center; border: 2px solid #fff;
  box-shadow: 0 1px 4px rgba(0,0,0,.55); pointer-events: auto; cursor: pointer; user-select: none; }
.kh-pin.on { background: var(--accent); z-index: 2; }
.kh-pin.far { opacity: .45; }
.kh-pin.pending { background: transparent; color: var(--brand); border: 2px dashed var(--brand); box-shadow: 0 0 0 2px #fff; pointer-events: none; }
.kh-pop { position: absolute; left: 24px; top: -8px; width: 240px; background: var(--card); color: var(--fg); text-align: left;
  border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; font: 13px/1.4 system-ui, sans-serif; cursor: default;
  box-shadow: 0 4px 14px rgba(0,0,0,.35); white-space: normal; overflow-wrap: anywhere; }
.kh-pop.left { left: auto; right: 24px; }
.kh-x { position: absolute; right: 4px; top: 2px; border: 0; background: none; color: var(--muted); font-size: 16px; cursor: pointer; }
.kh-meta { display: flex; align-items: center; gap: 6px; font-size: 13px; }
.kh-meta img { border-radius: 50%; }
.kh-count { font-weight: 400; }
.kh-status { font-size: 12px; margin-bottom: 6px; }
.kh-err, .kh-msg { color: var(--bad); }
.kh-msg { font-size: 12px; margin-top: 6px; }
.kh-form:not([hidden]) { display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--line); }
.kh-hint { font-size: 12px; color: var(--muted); }
.kh-row { display: flex; gap: 8px; }
.kh-form label { display: flex; flex-direction: column; flex: 1; min-width: 0; gap: 2px; font-size: 12px; color: var(--muted); }
.kh-form input, .kh-form textarea, .kh-token input { border: 1px solid var(--line); background: var(--bg); color: var(--fg); border-radius: 6px;
  padding: 4px 6px; font: inherit; font-size: 13px; width: 100%; min-width: 0; }
.kh-form textarea { resize: vertical; }
.kh-actions { display: flex; gap: 6px; flex-wrap: wrap; }
.kh-manual { width: 100%; margin-top: 6px; font: 12px ui-monospace, Consolas, monospace; color: var(--fg); background: var(--bg); }
.kh-item { display: flex; gap: 8px; padding: 8px 4px; border-top: 1px solid var(--line); cursor: pointer; font-size: 13px; }
.kh-item:hover, .kh-item.on { background: var(--soft); }
.kh-num { flex: none; width: 20px; height: 20px; border-radius: 50%; background: var(--brand); color: #fff; font-size: 11px; font-weight: 700;
  display: grid; place-items: center; margin-top: 1px; }
.kh-item.on .kh-num { background: var(--accent); }
.kh-main { min-width: 0; flex: 1; }
.kh-where { font-size: 12px; }
.kh-text { white-space: pre-wrap; overflow-wrap: anywhere; margin: 2px 0; }
.kh-other { padding-top: 6px; font-size: 12px; }
.kh-token { margin-top: 10px; font-size: 12px; border-top: 1px solid var(--line); padding-top: 8px; }
.kh-token summary { cursor: pointer; color: var(--muted); }
.kh-token form { display: flex; gap: 6px; }
.kh-foot { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 10px; font-size: 12px; }
`;
    document.head.appendChild(s);
  }

  /* The viewer is also told about clicks on the markers (delegated: the layer is rebuilt often) */
  function markerClicks() {
    document.addEventListener("click", e => {
      const pin = e.target.closest && e.target.closest(".kh-pin"); if (!pin || !pin.dataset.id) return;
      if (e.target.closest("a")) return;
      if (e.target.closest(".kh-x")) { S.active = null; renderAll(); return; }
      if (e.target.closest(".kh-pop")) return;
      activate(+pin.dataset.id, false);
    });
  }

  window.KHPins = {
    init: function (c) {
      if (S.started) return;
      ctx = c; D = c.D; S.started = true;
      injectStyle(); markerClicks();
      document.addEventListener("kh:viewer", e => attach(e.detail.kind, e.detail.viewer));
      document.addEventListener("keydown", e => { if (e.key === "Escape" && S.mode) cancel(); });
      // Measure (viewer-tools.js) was entered: leave the Comment mode
      document.addEventListener("kh:mode", e => { if (e.detail && e.detail.mode !== "comment" && S.mode) cancel(); });
      for (const [kind, v] of Object.entries(c.viewers)) attach(kind, v);
      load();
      requestAnimationFrame(tick);
    },
  };
})();
