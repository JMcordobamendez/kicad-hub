/* kicad-hub: design variants in the viewer.
 *
 * Loaded by scripts/portal.html on every build page whose design has variants (D.variants, filled by
 * scripts/portal.py from variants.json and the per-variant BOMs of the action). It adds, to the toolbar of the
 * Schematic and PCB tabs:
 *   - a "Variant" picker (default | each variant), the same in both tabs and kept in the URL
 *     (?variant=lite, like pins.js's ?at=), so a link opens that variant;
 *   - a legend ("lite: 5 parts not fitted · 1 part changed");
 *   - PCB: the footprints not fitted in the variant are drawn over the board: a hatched box over the
 *     footprint's courtyard (else its pads and graphics), a red X and a "DNP" label. Only the visible side
 *     is drawn strongly; the other side's boxes are faint outlines;
 *   - schematic: the symbols not fitted get KiCad's own DNP look (the red X over the body and pins): the
 *     viewer already draws it for a symbol with its DNP flag, so the flag is set according to the variant
 *     and the sheet repainted (and again after each change of sheet);
 *   - the "Selected" card (portal.html) reads the variant's values through KHVariants.part().
 *
 * The overlay is an SVG over the canvas, repositioned on camera changes (viewer.viewport.camera:
 * world_to_screen, canvas-relative CSS pixels, world units = mm of the file's coordinates, y down), like
 * scripts/viewer-tools.js and scripts/pins.js. It never catches clicks (pointer-events: none), so Comment,
 * Measure and the net highlight keep working.
 */
(function () {
  "use strict";

  const SVGNS = "http://www.w3.org/2000/svg";
  const PARAM = "variant";
  const MIN_LABEL_PX = 14;     // boxes narrower than this get no "DNP" label
  const MIN_CROSS_PX = 10;     // ... and no cross

  let ctx = null;
  const V = {cur: "", started: false, views: {}, byName: new Map(), sets: new Map(), orig: new WeakMap()};

  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c =>
    ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const camOf = v => v && v.viewport && v.viewport.camera;
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const el = (name, attrs, parent) => {
    const e = document.createElementNS(SVGNS, name);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  };

  /* ---------- the data ---------- */
  function defaultDnp() {
    if (!V.sets.has("")) V.sets.set("", new Set(Object.keys(ctx.D.parts || {}).filter(r => ctx.D.parts[r].dnp)));
    return V.sets.get("");
  }
  function dnpSet(name) {
    if (!name) return defaultDnp();
    if (!V.sets.has(name)) V.sets.set(name, new Set((V.byName.get(name) || {}).dnp || []));
    return V.sets.get(name);
  }
  const notFitted = (name, ref) => dnpSet(name).has(ref);

  /* ---------- the picker, in the URL and in both toolbars ---------- */
  function fromUrl() {
    let name = "";
    try { name = new URLSearchParams(location.search).get(PARAM) || ""; } catch (e) { /* no query */ }
    const hit = [...V.byName.keys()].find(n => n === name) || [...V.byName.keys()].find(n => n.toLowerCase() === name.toLowerCase());
    return hit || "";
  }
  function toUrl() {
    try {
      const u = new URL(location.href);
      if (V.cur) u.searchParams.set(PARAM, V.cur); else u.searchParams.delete(PARAM);
      history.replaceState(history.state, "", u.pathname + u.search + u.hash);
    } catch (e) { /* the page still works without the link */ }
  }
  function legend() {
    const n = dnpSet(V.cur).size, entry = V.byName.get(V.cur);
    const changed = entry ? Object.keys(entry.set || {}).length : 0;
    if (!V.cur) return {text: n ? `default: ${plural(n, "part")} not fitted` : "", title: n ? [...dnpSet("")].join(", ") : ""};
    const bits = [`${plural(n, "part")} not fitted`];
    if (changed) bits.push(`${changed} changed`);
    const title = [entry.description, n ? "Not fitted: " + [...dnpSet(V.cur)].join(", ") : "",
                   changed ? "Changed: " + Object.keys(entry.set).join(", ") : ""].filter(Boolean).join("\n");
    return {text: `${V.cur}: ${bits.join(" · ")}`, title: title};
  }
  function setVariant(name, from) {
    if (name === V.cur && from) return;
    V.cur = name;
    toUrl();
    const lg = legend();
    for (const view of Object.values(V.views)) {
      view.select.value = name;
      view.legend.textContent = lg.text; view.legend.title = lg.title; view.legend.hidden = !lg.text;
      view.rev++; view.key = null;
    }
    syncSchematic();
    if (ctx.reshow) ctx.reshow();
  }

  /* ---------- PCB: footprints not fitted ---------- */
  // local box of a footprint (its courtyard and pads; without a courtyard, its graphics too), in mm
  function localBox(fp) {
    let b = null;
    const grow = (x0, y0, x1, y1) => {
      if (![x0, y0, x1, y1].every(Number.isFinite)) return;
      b = b ? {x0: Math.min(b.x0, x0), y0: Math.min(b.y0, y0), x1: Math.max(b.x1, x1), y1: Math.max(b.y1, y1)} : {x0, y0, x1, y1};
    };
    const box = d => { try { const q = d.bbox; if (q && (q.w || q.h)) grow(q.x, q.y, q.x + q.w, q.y + q.h); } catch (e) { /* no box */ } };
    const draws = (fp.drawings || []).filter(d => !(typeof d.text === "string"));   // not the texts
    const yard = draws.filter(d => /\.CrtYd$/.test(d.layer || ""));
    for (const d of yard.length ? yard : draws) box(d);
    for (const pad of fp.pads || []) {
      const q = (pad.at && pad.at.position) || {x: 0, y: 0}, s = pad.size || {x: 1, y: 1};
      const h = Math.max(Number(s.x) || 0, Number(s.y) || 0, 0.4) / 2;
      grow(q.x - h, q.y - h, q.x + h, q.y + h);
    }
    return b || {x0: -1, y0: -1, x1: 1, y1: 1};
  }
  // the four corners of the box in board coordinates (the footprint's translation * rotation, as viewer-tools.js)
  function corners(fp) {
    const at = fp.at || {}, p0 = at.position || {x: 0, y: 0};
    const rad = (at.rotation || 0) * Math.PI / 180, cos = Math.cos(rad), sin = Math.sin(rad);
    const b = fp._khBox || (fp._khBox = localBox(fp)), m = 0.1;   // a little air around the courtyard
    return [[b.x0 - m, b.y0 - m], [b.x1 + m, b.y0 - m], [b.x1 + m, b.y1 + m], [b.x0 - m, b.y1 + m]]
      .map(([x, y]) => ({x: p0.x + x * cos + y * sin, y: p0.y - x * sin + y * cos}));
  }
  function footprints(view) {
    if (!view.fps) {
      view.fps = new Map();
      for (const fp of view.v.document.footprints || []) {
        if (!fp.reference) continue;
        (view.fps.get(fp.reference) || view.fps.set(fp.reference, []).get(fp.reference)).push(fp);
      }
    }
    return view.fps;
  }

  function render(view) {
    const v = view.v, cam = camOf(v);
    if (!cam || !view.embed.offsetParent) return;
    const cr = v.canvas.getBoundingClientRect(), hr = view.host.getBoundingClientRect();
    const key = [cam.center.x, cam.center.y, cam.zoom, cam.flipped, cam.viewport_size.x, cam.viewport_size.y,
                 cr.left - hr.left, cr.top - hr.top, cr.width, cr.height, view.rev].join();
    if (key === view.key) return;
    view.key = key;
    const L = view.layer.style;
    L.left = (cr.left - hr.left - view.host.clientLeft) + "px"; L.top = (cr.top - hr.top - view.host.clientTop) + "px";
    L.width = cr.width + "px"; L.height = cr.height + "px";
    const svg = view.svg;
    svg.setAttribute("width", cr.width); svg.setAttribute("height", cr.height);
    svg.textContent = "";
    const refs = [...dnpSet(V.cur)];
    if (!refs.length) return;

    const defs = el("defs", {}, svg), pat = el("pattern", {id: "kv-hatch", width: 7, height: 7, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)"}, defs);
    el("rect", {width: 7, height: 7, class: "kv-fill"}, pat);
    el("line", {x1: 0, y1: 0, x2: 0, y2: 7, class: "kv-hatch"}, pat);
    const flipped = !!cam.flipped;
    const map = footprints(view);
    for (const ref of refs) for (const fp of map.get(ref) || []) {
      const pts = corners(fp).map(p => cam.world_to_screen(p));
      const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
      const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
      if (x1 < 0 || y1 < 0 || x0 > cr.width || y0 > cr.height) continue;
      const strong = (fp.layer === "B.Cu") === flipped;   // on the side being looked at
      const g = el("g", {class: strong ? "kv-dnp" : "kv-dnp kv-far"}, svg);
      const poly = el("polygon", {points: pts.map(p => p.x.toFixed(1) + "," + p.y.toFixed(1)).join(" ")}, g);
      if (strong) poly.setAttribute("fill", "url(#kv-hatch)");
      const w = x1 - x0, h = y1 - y0;
      if (!strong) continue;
      if (Math.min(w, h) >= MIN_CROSS_PX) {
        el("line", {x1: pts[0].x, y1: pts[0].y, x2: pts[2].x, y2: pts[2].y, class: "kv-x"}, g);
        el("line", {x1: pts[1].x, y1: pts[1].y, x2: pts[3].x, y2: pts[3].y, class: "kv-x"}, g);
      }
      if (w >= MIN_LABEL_PX) {
        const big = Math.min(w, h) >= 30, cx = (x0 + x1) / 2;
        const cy = big ? (y0 + y1) / 2 : y0 - 6;   // inside a big box, above a small one
        const t = el("text", {x: cx, y: cy, class: "kv-label", "text-anchor": "middle", "dominant-baseline": "central"}, g);
        t.textContent = "DNP";
      }
    }
  }

  function attachPcb(view) {
    view.layer = document.createElement("div");
    view.layer.className = "kv-layer";
    view.svg = el("svg", {"aria-hidden": "true"});
    view.svg.setAttribute("class", "kv-svg");
    view.layer.appendChild(view.svg);
    view.host.appendChild(view.layer);
  }

  /* ---------- schematic: KiCad's own DNP mark ---------- */
  // The viewer paints a red X over the body and pins of a symbol whose `dnp` flag is set. The flag comes from
  // the file, so set it from the variant and repaint; the sheets are separate documents, so look again
  // whenever the active one changes (or is reloaded under another sheet path, which renames the references).
  function syncSchematic() {
    const view = V.views.sch;
    if (!view) return;
    const doc = view.v.document;
    if (!doc || !doc.symbols || typeof view.v.paint !== "function") return;
    let changed = false;
    for (const sym of doc.symbols.values()) {
      if (!V.orig.has(sym)) V.orig.set(sym, !!sym.dnp);     // what the file says, the default build
      const ref = sym.reference, known = ctx.D.parts && ctx.D.parts[ref];
      const want = !V.cur || !known ? V.orig.get(sym) : notFitted(V.cur, ref);
      if (!!sym.dnp !== want) { sym.dnp = want; changed = true; }
    }
    if (!changed) return;
    const picked = view.v.selected;
    view.v.paint(); view.v.draw();
    try { if (picked) view.v.selected = picked; } catch (e) { /* the selection is only cosmetic here */ }
  }

  /* ---------- toolbar ---------- */
  function attach(kind, v) {
    if (V.views[kind] || !ctx) return;
    const embed = document.getElementById("kc-" + kind);
    const host = embed && embed.closest(".viewer");
    const bar = host && host.querySelector(".vbar");
    if (!host || !bar || !v.canvas) return;
    const view = {kind, v, embed, host, key: null, rev: 0, fps: null};
    V.views[kind] = view;

    const box = document.createElement("label");
    box.className = "kv-pick";
    box.title = "Design variant: parts not fitted are marked on the board and the schematic";
    box.append("Variant ");
    view.select = document.createElement("select");
    view.select.className = "btn"; view.select.setAttribute("aria-label", "Design variant");
    view.select.innerHTML = `<option value="">default</option>` +
      ctx.D.variants.map(x => `<option value="${esc(x.name)}">${esc(x.name)}</option>`).join("");
    view.select.value = V.cur;
    view.select.addEventListener("change", () => setVariant(view.select.value));
    box.appendChild(view.select);
    view.legend = document.createElement("span");
    view.legend.className = "kv-legend"; view.legend.setAttribute("role", "status");
    const lg = legend();
    view.legend.textContent = lg.text; view.legend.title = lg.title; view.legend.hidden = !lg.text;
    const hint = bar.querySelector(".hint");
    bar.insertBefore(box, hint); bar.insertBefore(view.legend, hint);

    if (kind === "pcb") attachPcb(view);
    else syncSchematic();
    if (ctx.reshow) ctx.reshow();
  }

  function tick() {
    for (const view of Object.values(V.views)) {
      if (view.kind !== "pcb") continue;
      try { render(view); } catch (e) { /* viewer reloading */ }
    }
    requestAnimationFrame(tick);
  }

  function injectStyle() {
    const s = document.createElement("style");
    s.textContent = `
.viewer { position: relative; }
.kv-layer { position: absolute; overflow: hidden; pointer-events: none; z-index: 3; }
.kv-svg { position: absolute; left: 0; top: 0; overflow: hidden; pointer-events: none; }
.kv-fill { fill: rgba(24,28,34,.55); }
.kv-hatch { stroke: #c4cad3; stroke-width: 1.6; opacity: .85; }
.kv-dnp polygon { stroke: #ff5a5f; stroke-width: 1.5; fill: rgba(24,28,34,.25); }
.kv-dnp.kv-far polygon { stroke-dasharray: 4 3; opacity: .45; fill: none; }
.kv-x { stroke: #ff5a5f; stroke-width: 1.5; opacity: .9; }
.kv-label { fill: #fff; stroke: #c4262b; stroke-width: 3; paint-order: stroke; stroke-linejoin: round; font: 700 10px/1 system-ui, sans-serif; letter-spacing: .04em; }
.kv-pick { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: var(--muted); }
.kv-legend { font-size: 12px; color: var(--bad, #cf222e); font-variant-numeric: tabular-nums; }
`;
    document.head.appendChild(s);
  }

  window.KHVariants = {
    init: function (c) {
      if (V.started) return;
      ctx = c; V.started = true;
      for (const x of c.D.variants || []) V.byName.set(x.name, x);
      V.cur = fromUrl();
      injectStyle();
      document.addEventListener("kh:viewer", e => attach(e.detail.kind, e.detail.viewer));
      for (const [kind, v] of Object.entries(c.viewers)) attach(kind, v);
      setInterval(syncSchematic, 300);   // a new sheet or a reload paints its symbols from the file
      requestAnimationFrame(tick);
    },
    // for the Selected card: what the chosen variant changes for this part, or null on the default build
    part: function (ref) {
      if (!V.started || !V.cur) return null;
      const x = V.byName.get(V.cur) || {};
      return {variant: V.cur, notFitted: notFitted(V.cur, ref), set: (x.set || {})[ref] || {},
              offers: (x.offers || {})[ref] || null, currency: x.currency || ""};
    },
    current: function () { return V.cur; },
  };
})();
