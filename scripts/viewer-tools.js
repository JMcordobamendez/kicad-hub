/* kicad-hub: net navigation and measuring in the viewer.
 *
 * Loaded by scripts/portal.html on every build page (branch, release, history, PR). It adds:
 *   - PCB: a "Nets" card in the side panel (type-ahead over every net of the board), net
 *     buttons in the "Selected" card, highlighting of a net (every track, via, pad and zone of
 *     it, the rest dimmed), and Prev / Next pad to walk through its pads;
 *   - PCB and schematic: a "Measure" mode in the viewer toolbar: click (or tap) point A, then
 *     point B; the distance, dx and dy are drawn over the viewer. On the PCB the points snap
 *     to the centre of a pad when the click is within a few pixels of one.
 *
 * The net highlight is KiCanvas's own (viewer.highlight_net, the one a click on a track
 * triggers: the net's items are painted into its overlay layer); we call it and wrap it, so
 * a click on a track also shows up in the card. KiCanvas has no schematic equivalent.
 * Everything else is drawn by us in an SVG over the canvas, repositioned on camera changes
 * (viewer.viewport.camera: screen_to_world / world_to_screen, canvas-relative CSS pixels,
 * world units = mm of the file's coordinates, y down), like the markers of scripts/pins.js.
 *
 * Modes are exclusive with pins.js's "Comment" mode: each side announces the mode it enters
 * with a `kh:mode` event ({detail: {mode: "measure" | "comment"}}) and leaves its own when it
 * sees the other one.
 */
(function () {
  "use strict";

  const SVGNS = "http://www.w3.org/2000/svg";
  const DIM = 0.4;          // opacity of the copper layers while a net is highlighted
  const SNAP_PX = 9;        // a click this close to a pad centre snaps to it
  const MAX_RESULTS = 12;

  let ctx = null;
  const T = {views: {}, measure: null, started: false};   // measure = kind of the view being measured, or null

  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c =>
    ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const natural = (a, b) => String(a).localeCompare(String(b), undefined, {numeric: true});
  const camOf = v => v && v.viewport && v.viewport.camera;
  const num = n => typeof n === "number" ? n : n && typeof n === "object" && typeof n.number === "number" ? n.number : null;
  const el = (name, attrs, parent) => {
    const e = document.createElementNS(SVGNS, name);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  };

  /* ---------- nets of the board ---------- */
  // number -> {n, name, pads: [{ref, pad, x, y, bottom}], tracks, len, vias, zones, box}
  function buildIndex(board) {
    const nets = new Map();
    const get = n => {
      let e = nets.get(n);
      if (!e) { e = {n: n, name: "", pads: [], tracks: 0, len: 0, vias: 0, zones: 0, box: null}; nets.set(n, e); }
      return e;
    };
    const grow = (e, x, y) => {
      const b = e.box || (e.box = {x0: x, y0: y, x1: x, y1: y});
      b.x0 = Math.min(b.x0, x); b.y0 = Math.min(b.y0, y); b.x1 = Math.max(b.x1, x); b.y1 = Math.max(b.y1, y);
    };
    for (const n of board.nets || []) if (n && n.number > 0 && n.name) get(n.number).name = n.name;

    for (const fp of board.footprints || []) {
      const at = fp.at || {}, p0 = at.position || {x: 0, y: 0};
      const rad = (at.rotation || 0) * Math.PI / 180, cos = Math.cos(rad), sin = Math.sin(rad);
      for (const pad of fp.pads || []) {
        const n = num(pad.net);
        if (!n || n < 1) continue;
        const e = get(n);
        if (!e.name && pad.net && pad.net.name) e.name = pad.net.name;
        const q = (pad.at && pad.at.position) || {x: 0, y: 0};
        // KiCanvas's footprint matrix: translation * rotation, rotation as in Matrix3.rotation
        const x = p0.x + q.x * cos + q.y * sin, y = p0.y - q.x * sin + q.y * cos;
        const ls = pad.layers || [];
        const bottom = pad.type === "smd" && ls.includes("B.Cu") && !ls.includes("F.Cu");
        e.pads.push({ref: fp.reference || "?", pad: String(pad.number == null ? "" : pad.number), x: x, y: y, bottom: bottom});
        grow(e, x, y);
      }
    }
    for (const s of board.segments || []) {
      const n = num(s.net); if (!n || n < 1) continue;
      const e = get(n);
      e.tracks++;
      if (s.start && s.end) {
        e.len += s.mid ? arcLength(s.start, s.mid, s.end) : Math.hypot(s.end.x - s.start.x, s.end.y - s.start.y);
        grow(e, s.start.x, s.start.y); grow(e, s.end.x, s.end.y);
      }
    }
    for (const v of board.vias || []) {
      const n = num(v.net); if (!n || n < 1) continue;
      const e = get(n);
      e.vias++;
      if (v.at && v.at.position) grow(e, v.at.position.x, v.at.position.y);
    }
    for (const z of board.zones || []) {
      const n = num(z.net); if (!n || n < 1) continue;
      const e = get(n);
      e.zones++;
      for (const poly of z.polygons || []) for (const pt of poly.polyline || poly.pts || []) grow(e, pt.x, pt.y);
    }
    const list = [...nets.values()].filter(e => e.name && (e.pads.length || e.tracks || e.vias || e.zones));
    for (const e of list) e.pads.sort((a, b) => natural(a.ref, b.ref) || natural(a.pad, b.pad));
    return {byNumber: new Map(list.map(e => [e.n, e])), list: list.sort((a, b) => natural(a.name, b.name))};
  }
  // length of the arc through three points (the chord if they are in line)
  function arcLength(a, m, b) {
    const d = 2 * (a.x * (m.y - b.y) + m.x * (b.y - a.y) + b.x * (a.y - m.y));
    if (Math.abs(d) < 1e-9) return Math.hypot(b.x - a.x, b.y - a.y);
    const ux = ((a.x ** 2 + a.y ** 2) * (m.y - b.y) + (m.x ** 2 + m.y ** 2) * (b.y - a.y) + (b.x ** 2 + b.y ** 2) * (a.y - m.y)) / d;
    const uy = ((a.x ** 2 + a.y ** 2) * (b.x - m.x) + (m.x ** 2 + m.y ** 2) * (a.x - b.x) + (b.x ** 2 + b.y ** 2) * (m.x - a.x)) / d;
    const r = Math.hypot(a.x - ux, a.y - uy);
    const ang = (p, q) => { let t = Math.atan2(q.y - uy, q.x - ux) - Math.atan2(p.y - uy, p.x - ux); while (t < 0) t += 2 * Math.PI; return t; };
    // the arc goes a -> m -> b: take the sweep that contains m
    const t1 = ang(a, b), tm = ang(a, m);
    return r * (tm <= t1 ? t1 : 2 * Math.PI - t1);
  }

  /* ---------- attaching to a viewer ---------- */
  function attach(kind, v) {
    if (T.views[kind] || !ctx) return;
    const embed = document.getElementById("kc-" + kind);
    const host = embed && embed.closest(".viewer");
    const bar = host && host.querySelector(".vbar");
    if (!host || !bar || !v.canvas) return;

    const view = {kind: kind, v: v, embed: embed, host: host, key: null, rev: 0, layersRef: null,
                  net: null, pad: -1, dimmed: [], m: {a: null, b: null, hover: null},
                  index: null, snap: [], paintNet: null};
    T.views[kind] = view;

    // toolbar: Measure toggle and its readout
    view.mbtn = document.createElement("button");
    view.mbtn.className = "btn"; view.mbtn.type = "button"; view.mbtn.textContent = "Measure";
    view.mbtn.title = "Measure a distance: click point A, then point B (Esc clears)";
    view.mbtn.setAttribute("aria-pressed", "false");
    view.read = document.createElement("span");
    view.read.className = "kt-read"; view.read.setAttribute("role", "status"); view.read.setAttribute("aria-live", "polite");
    const hint = bar.querySelector(".hint");
    bar.insertBefore(view.mbtn, hint); bar.insertBefore(view.read, hint);
    view.mbtn.addEventListener("click", () => T.measure === kind ? setMeasure(null) : setMeasure(kind));

    // overlay over the canvas
    view.layer = document.createElement("div");
    view.layer.className = "kt-layer";
    view.svg = el("svg", {"aria-hidden": "true"});
    view.svg.setAttribute("class", "kt-svg");
    view.layer.appendChild(view.svg);
    view.label = document.createElement("div");
    view.label.className = "kt-label"; view.label.hidden = true;
    view.layer.appendChild(view.label);
    host.appendChild(view.layer);

    if (kind === "pcb") {
      try { view.index = buildIndex(v.document); } catch (e) { view.index = null; }
      if (view.index) {
        view.snap = [].concat(...view.index.list.map(e => e.pads));
        // KiCanvas's own click on a track lands here too
        if (typeof v.highlight_net === "function") {
          view.paintNet = v.highlight_net.bind(v);
          v.highlight_net = n => setNet(view, n, {});
        }
        buildCard(view);
      }
    }

    // a selection (a click on a part or on empty board) repaints KiCanvas's overlay: the net highlight is gone
    v.addEventListener("kicanvas:select", () => { if (view.net != null) clearNet(view, true); });

    // clicks while measuring, caught on the embed (capture phase) so that they never reach KiCanvas: they must
    // not select the parts under the points, and the first one must count even if KiCanvas's "Click or tap
    // to interact" cover is still up (it eats the first click on the viewer)
    const onCanvas = e => { const p = e.composedPath(); return p.includes(v.canvas) || p.some(n => n.localName === "kc-ui-focus-overlay"); };
    embed.addEventListener("pointerdown", e => { if (T.measure === kind && onCanvas(e)) view.down = {x: e.clientX, y: e.clientY}; }, true);
    embed.addEventListener("click", e => {
      if (T.measure !== kind || !onCanvas(e)) return;
      e.stopPropagation();
      if (view.down && Math.hypot(e.clientX - view.down.x, e.clientY - view.down.y) > 5) return;   // it was a pan
      const pt = pointAt(view, e);
      if (!pt) return;
      const m = view.m;
      if (!m.a || m.b) { m.a = pt; m.b = null; } else { m.b = pt; }
      m.hover = null;
      changed(view);
    }, true);
    embed.addEventListener("pointermove", e => {
      if (T.measure !== kind || e.pointerType !== "mouse" || !onCanvas(e)) return;
      view.m.hover = pointAt(view, e);
      changed(view);
    });
    embed.addEventListener("pointerleave", () => { if (view.m.hover) { view.m.hover = null; changed(view); } });

    changed(view);
  }

  const changed = view => { view.rev++; view.key = null; render(view); readout(view); };

  /* ---------- the Nets card ---------- */
  function buildCard(view) {
    const sel = document.getElementById("sel-pcb");
    if (!sel) return;
    const card = document.createElement("div");
    card.className = "card side kt-card";
    card.innerHTML = `<h3>Nets</h3>
      <input type="search" class="kt-q" placeholder="Search ${view.index.list.length} nets…" autocomplete="off" spellcheck="false" aria-label="Search nets">
      <div class="kt-results" role="list"></div>
      <div class="kt-active" hidden>
        <div class="kt-name"></div><div class="kt-stats muted"></div>
        <div class="kt-row">
          <button class="btn" type="button" data-kt="prev" aria-label="Previous pad of the net">&larr; Prev pad</button>
          <button class="btn" type="button" data-kt="next" aria-label="Next pad of the net">Next pad &rarr;</button>
          <button class="btn" type="button" data-kt="fit">Fit</button>
          <button class="btn" type="button" data-kt="clear">Clear</button>
        </div>
        <div class="kt-pad" role="status" aria-live="polite"></div>
      </div>
      <div class="kt-hint muted">Click a net here, in the Selected card or a track on the board. Esc clears.</div>`;
    sel.insertAdjacentElement("afterend", card);
    view.card = card;
    view.q = card.querySelector(".kt-q");
    view.results = card.querySelector(".kt-results");

    const search = () => {
      const q = view.q.value.trim().toLowerCase();
      if (!q) { view.results.innerHTML = ""; return; }
      const score = e => { const n = e.name.toLowerCase(), last = n.split("/").pop();
        return n === q || last === q ? 0 : last.startsWith(q) ? 1 : n.includes(q) ? 2 : 9; };
      const hits = view.index.list.map(e => [score(e), e]).filter(h => h[0] < 9).sort((a, b) => a[0] - b[0] || natural(a[1].name, b[1].name)).slice(0, MAX_RESULTS);
      view.results.innerHTML = hits.map(h => `<button class="kt-hit" type="button" role="listitem" data-net="${h[1].n}">` +
        `<span>${esc(h[1].name)}</span><span class="muted">${h[1].pads.length} pad${h[1].pads.length === 1 ? "" : "s"}</span></button>`).join("") ||
        `<div class="muted kt-none">No net matches.</div>`;
    };
    view.q.addEventListener("input", search);
    view.q.addEventListener("keydown", e => {
      if (e.key === "Enter") { const first = view.results.querySelector("[data-net]"); if (first) { e.preventDefault(); setNet(view, +first.dataset.net, {fit: true}); } }
      else if (e.key === "ArrowDown") { const first = view.results.querySelector("[data-net]"); if (first) { e.preventDefault(); first.focus(); } }
    });
    view.results.addEventListener("keydown", e => {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      const b = e.target.closest("[data-net]"); if (!b) return;
      e.preventDefault();
      const n = e.key === "ArrowDown" ? b.nextElementSibling : b.previousElementSibling;
      if (n && n.dataset && n.dataset.net) n.focus(); else if (e.key === "ArrowUp") view.q.focus();
    });
    card.addEventListener("click", e => {
      const b = e.target.closest("[data-kt]"); if (!b) return;
      const k = b.dataset.kt;
      if (k === "clear") clearNet(view, false);
      else if (k === "next") step(view, 1);
      else if (k === "prev") step(view, -1);
      else if (k === "fit") fit(view);
    });
  }

  function renderCard(view) {
    if (!view.card) return;
    const box = view.card.querySelector(".kt-active"), e = view.net != null && view.index.byNumber.get(view.net);
    // scripts/crossprobe.js adds Show in schematic to this card for the net
    document.dispatchEvent(new CustomEvent("kh:net", {detail: {net: e ? e.n : null, name: e ? e.name : ""}}));
    box.hidden = !e;
    view.card.querySelector(".kt-hint").hidden = !!e;
    if (!e) return;
    view.card.querySelector(".kt-name").textContent = e.name;
    const bits = [`${e.pads.length} pad${e.pads.length === 1 ? "" : "s"}`, `${e.tracks} track${e.tracks === 1 ? "" : "s"}` + (e.len ? ` (${e.len.toFixed(1)} mm)` : ""),
      `${e.vias} via${e.vias === 1 ? "" : "s"}`, `${e.zones} zone${e.zones === 1 ? "" : "s"}`];
    view.card.querySelector(".kt-stats").textContent = bits.join(" · ");
    for (const b of view.card.querySelectorAll('[data-kt="prev"],[data-kt="next"]')) b.disabled = !e.pads.length;
    const p = e.pads[view.pad];
    view.card.querySelector(".kt-pad").textContent = p ? `${p.ref} pad ${p.pad} · ${view.pad + 1} of ${e.pads.length}` : (e.pads.length ? `${e.pads.length} pads: use Next pad to visit them.` : "");
  }

  /* ---------- highlighting ---------- */
  function setNet(view, n, opts) {
    const e = view.index && view.index.byNumber.get(n);
    if (!e) { if (view.paintNet) view.paintNet(n); return; }   // a net with nothing to list: KiCanvas alone
    const v = view.v;
    if (view.net != null && view.net !== n) undim(view);
    view.net = n; view.pad = -1;
    try { view.paintNet(n); } catch (err) { return; }   // KiCanvas's own highlight, into its overlay layer
    dim(view);
    v.draw();
    view.layersRef = v.layers;
    renderCard(view);
    if (opts.fit) fit(view);   // chosen from a list (not a click on a track): show the whole net
    changed(view);
  }
  function clearNet(view, selectionTookOver) {
    if (view.net == null) return;
    view.net = null; view.pad = -1;
    undim(view);
    try { if (!selectionTookOver) view.v.paint_selected(); } catch (e) { /* ignore */ }
    view.v.draw();
    renderCard(view); changed(view);
  }
  function dim(view) {
    const L = view.v.layers; if (!L || view.dimmed.length) return;
    const all = [];
    for (const gen of ["copper_layers", "via_layers", "zone_layers", "pad_layers"]) {
      try { all.push(...L[gen]()); } catch (e) { /* layer set without it */ }
    }
    for (const l of new Set(all)) if (l) { view.dimmed.push([l, l.opacity]); l.opacity = DIM; }
  }
  function undim(view) {
    // after KiCanvas rebuilt its layers (flip, theme) the old objects are gone and the new ones are at full opacity
    for (const [l, o] of view.dimmed) l.opacity = o;
    view.dimmed = [];
  }

  /* ---------- pads of the net ---------- */
  function step(view, dir) {
    const e = view.net != null && view.index.byNumber.get(view.net);
    if (!e || !e.pads.length) return;
    const n = e.pads.length;
    view.pad = view.pad < 0 ? (dir > 0 ? 0 : n - 1) : (view.pad + dir + n) % n;
    const p = e.pads[view.pad], cam = camOf(view.v);
    // smd pad on the other side of the board: turn the view (the portal's Top / Bottom buttons)
    if (cam && p.bottom !== !!cam.flipped) {
      const b = document.querySelector(`[data-side="${p.bottom ? "bottom" : "top"}"]`);
      if (b) b.click();
    }
    const cam2 = camOf(view.v);
    if (cam2) {
      const w = Math.max(4, Math.min(cam2.bbox.w, 14));   // keep a close zoom, never farther than 14 mm across
      cam2.bbox = {x: p.x - w / 2, y: p.y - w / 2, w: w, h: w};
    }
    view.v.draw();
    renderCard(view); changed(view);
  }
  function fit(view) {
    const e = view.net != null && view.index.byNumber.get(view.net), cam = camOf(view.v);
    if (!e || !e.box || !cam) return;
    const b = e.box, w = Math.max(b.x1 - b.x0, 4), h = Math.max(b.y1 - b.y0, 4), m = Math.max(w, h) * 0.15;
    cam.bbox = {x: b.x0 - m, y: b.y0 - m, w: w + 2 * m, h: h + 2 * m};
    view.v.draw();
    changed(view);
  }

  /* ---------- measuring ---------- */
  function findCover(root) {
    for (const n of root.querySelectorAll("*")) {
      if (n.localName === "kc-ui-focus-overlay") return n;
      if (n.shadowRoot) { const f = findCover(n.shadowRoot); if (f) return f; }
    }
    return null;
  }
  function setMeasure(kind) {
    T.measure = kind;
    const target = T.views[kind];
    // wake the viewer (drop the "Click or tap to interact" cover) once the click on the button is over:
    // KiCanvas puts the cover back on every click outside the viewer
    if (target) setTimeout(() => { const c = findCover(target.embed.shadowRoot || target.embed); if (c) c.classList.add("has-focus"); }, 0);
    for (const view of Object.values(T.views)) {
      const on = view.kind === kind;
      view.mbtn.classList.toggle("on", on);
      view.mbtn.setAttribute("aria-pressed", on ? "true" : "false");
      try { view.v.canvas.style.cursor = on ? "crosshair" : ""; } catch (e) { /* cosmetic */ }
      if (!on) { view.m = {a: null, b: null, hover: null}; }
      changed(view);
    }
    if (kind) document.dispatchEvent(new CustomEvent("kh:mode", {detail: {mode: "measure", kind: kind}}));
  }
  // a point of the design under a mouse or touch event: world coordinates, snapped to a pad centre if one is near
  function pointAt(view, e) {
    const v = view.v, cam = camOf(v); if (!cam) return null;
    const r = v.canvas.getBoundingClientRect();
    const w = cam.screen_to_world({x: e.clientX - r.left, y: e.clientY - r.top});
    const pt = {x: w.x, y: w.y, label: ""};
    if (view.snap.length && cam.zoom > 0) {
      const lim = SNAP_PX / cam.zoom;
      let best = null, bd = lim * lim;
      for (const p of view.snap) {
        const dx = p.x - w.x, dy = p.y - w.y, d = dx * dx + dy * dy;
        if (d < bd) { bd = d; best = p; }
      }
      if (best) { pt.x = best.x; pt.y = best.y; pt.snap = true; pt.label = `${best.ref} pad ${best.pad}`; }
    }
    return pt;
  }
  function dist(a, b) {
    const dx = Math.abs(b.x - a.x), dy = Math.abs(b.y - a.y);
    return {d: Math.hypot(dx, dy), dx: dx, dy: dy};
  }
  const mm = n => n.toFixed(2);
  function text(a, b) {
    const t = dist(a, b);
    return `${mm(t.d)} mm (dx ${mm(t.dx)} · dy ${mm(t.dy)})`;
  }
  function readout(view) {
    const m = view.m, on = T.measure === view.kind;
    let s = "";
    if (on) {
      if (!m.a) s = "Measure: click point A";
      else if (!m.b) s = "Measure: click point B" + (m.hover ? " · " + text(m.a, m.hover) : "");
      else s = `${m.a.label ? m.a.label + " → " : ""}${m.b.label ? m.b.label + ": " : ""}${text(m.a, m.b)}`;
    }
    view.read.textContent = s;
  }

  /* ---------- drawing over the canvas ---------- */
  function render(view, force) {
    const v = view.v, cam = camOf(v);
    if (!cam || !view.embed.offsetParent) return;
    const cr = v.canvas.getBoundingClientRect(), hr = view.host.getBoundingClientRect();
    const key = [cam.center.x, cam.center.y, cam.zoom, cam.flipped, cam.viewport_size.x, cam.viewport_size.y,
                 cr.left - hr.left, cr.top - hr.top, cr.width, cr.height, view.rev].join();
    if (!force && key === view.key) return;
    view.key = key;
    const L = view.layer.style;
    L.left = (cr.left - hr.left - view.host.clientLeft) + "px"; L.top = (cr.top - hr.top - view.host.clientTop) + "px";
    L.width = cr.width + "px"; L.height = cr.height + "px";
    const S = p => cam.world_to_screen({x: p.x, y: p.y});

    const svg = view.svg;
    svg.setAttribute("width", cr.width); svg.setAttribute("height", cr.height);
    svg.textContent = "";

    // a ring on each pad of the net (the highlight alone is easy to miss on a thin net), the one being visited bigger
    const e = view.net != null && view.index && view.index.byNumber.get(view.net), cur = e && e.pads[view.pad];
    if (e && e.pads.length <= 400) {
      for (const p of e.pads) { const s = S(p); el("circle", {cx: s.x, cy: s.y, r: 6, class: "kt-padmark"}, svg); }
    }
    if (cur) {
      const s = S(cur);
      el("circle", {cx: s.x, cy: s.y, r: 15, class: "kt-ring-halo"}, svg);
      el("circle", {cx: s.x, cy: s.y, r: 15, class: "kt-ring"}, svg);
    }

    // the measurement
    const m = view.m, on = T.measure === view.kind;
    let lab = null;
    if (on && m.hover && m.hover.snap && (!m.a || !m.b)) {
      const s = S(m.hover);
      el("circle", {cx: s.x, cy: s.y, r: 9, class: "kt-snap"}, svg);
    }
    if (m.a) {
      const end = m.b || (on ? m.hover : null), a = S(m.a);
      if (end) {
        const b = S(end);
        el("line", {x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: "kt-line-halo"}, svg);
        el("line", {x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: "kt-line" + (m.b ? "" : " kt-live")}, svg);
        // the legs of dx and dy
        if (m.b) {
          const c = {x: b.x, y: a.y};
          el("polyline", {points: `${a.x},${a.y} ${c.x},${c.y} ${b.x},${b.y}`, class: "kt-legs"}, svg);
        }
        lab = {x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, t: text(m.a, end)};
        el("circle", {cx: b.x, cy: b.y, r: 4.5, class: "kt-dot"}, svg);
      }
      el("circle", {cx: a.x, cy: a.y, r: 4.5, class: "kt-dot"}, svg);
    }
    if (lab) {
      view.label.hidden = false;
      view.label.textContent = lab.t;
      view.label.style.left = lab.x + "px"; view.label.style.top = lab.y + "px";
    } else view.label.hidden = true;
  }

  function tick() {
    for (const view of Object.values(T.views)) {
      try {
        // KiCanvas rebuilt its layers (flip, theme change): its overlay and our dimming are gone
        if (view.net != null && view.layersRef && view.v.layers !== view.layersRef) {
          view.dimmed = []; view.layersRef = view.v.layers;
          view.paintNet(view.net); dim(view); view.v.draw();
        }
        render(view);
      } catch (e) { /* viewer reloading */ }
    }
    requestAnimationFrame(tick);
  }

  /* ---------- styles and global events ---------- */
  function injectStyle() {
    const s = document.createElement("style");
    s.textContent = `
.viewer { position: relative; }
.kt-layer { position: absolute; overflow: hidden; pointer-events: none; z-index: 4; }
.kt-svg { position: absolute; left: 0; top: 0; overflow: hidden; }
.kt-line-halo, .kt-ring-halo { stroke: rgba(0,0,0,.65); stroke-width: 5; fill: none; }
.kt-line { stroke: #ffd400; stroke-width: 2; fill: none; stroke-linecap: round; }
.kt-line.kt-live { stroke-dasharray: 6 4; }
.kt-legs { stroke: #ffd400; stroke-width: 1; stroke-dasharray: 3 3; fill: none; opacity: .75; }
.kt-dot { fill: #ffd400; stroke: #000; stroke-width: 1.5; }
.kt-snap { fill: none; stroke: #ffd400; stroke-width: 2; }
.kt-padmark { fill: none; stroke: #ffd400; stroke-width: 1.5; opacity: .9; }
.kt-ring { stroke: var(--brand, #e8622c); stroke-width: 3; fill: none; stroke-dasharray: 5 3; }
.kt-label { position: absolute; transform: translate(-50%, -135%); background: rgba(20,24,30,.92); color: #fff; border: 1px solid #ffd400;
  border-radius: 6px; padding: 2px 8px; font: 600 12px/1.4 ui-monospace, Consolas, monospace; white-space: nowrap; }
.kt-read { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.kt-q { width: 100%; border: 1px solid var(--line); background: var(--bg); color: var(--fg); border-radius: 6px; padding: 5px 8px; font: inherit; font-size: 13px; }
.kt-results { display: flex; flex-direction: column; margin-top: 4px; max-height: 260px; overflow-y: auto; }
.kt-hit { display: flex; justify-content: space-between; gap: 8px; text-align: left; background: none; border: 0; border-top: 1px solid var(--line);
  padding: 5px 4px; color: var(--fg); font: inherit; font-size: 13px; cursor: pointer; }
.kt-hit span:first-child { overflow-wrap: anywhere; min-width: 0; }
.kt-hit span:last-child { flex: none; font-size: 12px; }
.kt-hit:hover, .kt-hit:focus-visible { background: var(--soft); }
.kt-none, .kt-hint { font-size: 12px; padding-top: 6px; }
.kt-active { margin-top: 10px; padding-top: 8px; border-top: 1px solid var(--line); }
.kt-name { font-weight: 700; overflow-wrap: anywhere; }
.kt-stats { font-size: 12px; margin-bottom: 8px; }
.kt-row { display: flex; gap: 6px; flex-wrap: wrap; }
.kt-row .btn { padding: 3px 9px; font-size: 12px; }
.kt-row .btn:disabled { opacity: .5; cursor: default; }
.kt-pad { margin-top: 8px; font-size: 13px; font-weight: 600; min-height: 1.4em; }
`;
    document.head.appendChild(s);
  }

  function globalEvents() {
    // buttons with data-net (the Selected card) highlight that net
    document.addEventListener("click", e => {
      const b = e.target.closest && e.target.closest("[data-net]"); if (!b) return;
      const view = T.views.pcb; if (!view || !view.index) return;
      const n = +b.dataset.net; if (!Number.isFinite(n)) return;
      setNet(view, n, {fit: true});
    });
    document.addEventListener("keydown", e => {
      if (e.key !== "Escape") return;
      for (const view of Object.values(T.views)) {
        if (view.net != null) clearNet(view, false);
        if (view.q && view.q.value) { view.q.value = ""; view.results.innerHTML = ""; }
      }
      if (T.measure) setMeasure(null);
    });
    // the Comment mode of pins.js was entered: leave Measure
    document.addEventListener("kh:mode", e => { if (e.detail && e.detail.mode !== "measure" && T.measure) setMeasure(null); });
  }

  window.KHTools = {
    init: function (c) {
      if (T.started) return;
      ctx = c; T.started = true;
      injectStyle(); globalEvents();
      document.addEventListener("kh:viewer", e => attach(e.detail.kind, e.detail.viewer));
      for (const [kind, v] of Object.entries(c.viewers)) attach(kind, v);
      requestAnimationFrame(tick);
    },
    // highlight a net of the board and frame it (the cross-probing buttons); false if it has nothing to show
    showNet: function (n) {
      const view = T.views.pcb, e = view && view.index && view.index.byNumber.get(n);
      if (!e) return false;
      setNet(view, n, {fit: true});
      return true;
    },
  };
})();
