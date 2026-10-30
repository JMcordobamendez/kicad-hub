/* kicad-hub: cross-probing between the PCB and the schematic.
 *
 * Loaded by scripts/portal.html on every build page that has both a schematic and a board. It adds:
 *   - PCB, "Selected" card: Show in schematic. It switches to the Schematic tab, opens the sheet that has
 *     the part (looked up on every sheet, with the references of each sheet instance), selects the symbol
 *     and zooms to it. A multi-unit part opens on its first unit; the other units are listed as buttons;
 *   - schematic, "Selected" card: Show on PCB. It switches to the PCB tab, turns the board to the side the
 *     footprint is on, selects it and zooms to it. A symbol without a footprint (power symbol, excluded
 *     from the board, not on the board) gets a message instead. Once the board has been opened, the
 *     nets of the part are listed as buttons that highlight them on the board;
 *   - schematic: a click on a wire, bus, junction, label, power symbol or pin picks its net (the KiCanvas
 *     fork in scripts/vendor builds the schematic's connectivity, see scripts/vendor/README.md). The net is
 *     highlighted on the sheet, dimming the rest, and follows you through the hierarchy. The "Net in the
 *     schematic" card names it, steps through the sheets where it is drawn (Prev / Next sheet) and has Show
 *     on PCB, which highlights the net on the board;
 *   - PCB, "Nets" card (scripts/viewer-tools.js): Show in schematic goes to the net in the schematic, found
 *     by the pins of the net (a pad is pin <number> of symbol <reference>), or by its name when it has no
 *     pins, and highlights it on the sheets where it is. With a viewer that has no schematic connectivity
 *     (an older KiCanvas) it falls back to marking each connected pin and each label / power symbol of the
 *     net with a ring (KiCanvas alone has no schematic net highlight);
 *   - a "Sync" toggle in both toolbars (off by default, kept in the URL as ?sync=1): selecting a part in
 *     one tab selects it in the other, and so does highlighting a net, so switching tabs lands on it.
 *
 * How things are found. Parts: the schematic index lists, per sheet instance (project.pages(), the pages
 * of the sheet picker), every symbol with the reference of that instance (the same lookup as KiCanvas's
 * update_hierarchical_data); on the board, the footprint with that reference. Nets: the fork's
 * viewer.netlist (SchematicNetlist) joins pins by wires, labels, power symbols and sheet pins; a net of the
 * board is its schematic net by the pins on its pads. Without the fork (the fallback) no netlist is
 * published, so the pins come from the board (position worked out like KiCanvas's pin painter does) and the
 * labels from the sheets' names: a global label or power symbol by its text, a local label as
 * "<sheet path>/<text>", like KiCad names nets.
 *
 * Everything drawn is an SVG over the canvas, repositioned on camera changes like scripts/viewer-tools.js
 * (camera.world_to_screen, canvas-relative CSS pixels, world units = mm of the file's coordinates).
 * The overlay never catches clicks, so Comment, Measure and the net highlight keep working, and the
 * buttons only act on a normal selection (they do not start or end any of those modes).
 */
(function () {
  "use strict";

  const SVGNS = "http://www.w3.org/2000/svg";
  const PARAM = "sync";
  const TAB = {pcb: "pcb", sch: "schematic"};
  const MAX_MARKS = 600;       // rings drawn per sheet

  let ctx = null;
  const X = {started: false, both: false, sync: false, views: {}, busy: 0, working: false, idx: null,
             last: null,      // the part last selected by the user: {kind, ref, seq}
             cur: {},         // per kind: the selection being shown in its Selected card
             msg: {},         // per kind: a note for the part shown in the card
             pcbNet: null,    // net highlighted on the board (from viewer-tools.js): {net, name}
             marks: null,     // net being shown in the schematic: {net, pages, cur, native}; native is the
                              // fork's SchematicNet (highlighted by the viewer), else the rings are ours
             lastNet: null,   // the net last picked by the user: {kind, snet | net, seq}
             seq: 0,          // order of the user's picks, to tell which one a tab opened in sync mode lands on
             netMsg: "", markId: 0};

  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c =>
    ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const camOf = v => v && v.viewport && v.viewport.camera;
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const num = n => typeof n === "number" ? n : n && typeof n === "object" && typeof n.number === "number" ? n.number : null;
  const letter = u => u >= 1 && u <= 26 ? String.fromCharCode(64 + u) : String(u);
  const other = kind => kind === "pcb" ? "sch" : "pcb";
  const el = (name, attrs, parent) => {
    const e = document.createElementNS(SVGNS, name);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  };

  /* ---------- tabs ---------- */
  const visible = kind => { const e = document.getElementById("kc-" + kind); return !!(e && e.offsetParent); };
  const shown = () => visible("pcb") ? "pcb" : visible("sch") ? "sch" : null;
  // switch to a tab and wait for its viewer (the portal builds a tab on its first visit)
  async function openTab(kind) {
    if (!visible(kind)) location.hash = TAB[kind];
    for (let i = 0; i < 200; i++) {
      const v = ctx.viewers[kind];
      if (v && v.document && X.views[kind] && visible(kind)) { await sleep(60); return v; }
      await sleep(150);
    }
    return null;
  }
  // the canvas has a size again after its tab was hidden
  async function settle(view) {
    for (let i = 0; i < 20 && !(view.v.canvas && view.v.canvas.clientWidth > 0); i++) await sleep(100);
    await sleep(120);
  }
  function focusCard(kind) {
    const box = document.getElementById("sel-" + kind);
    if (!box) return;
    box.setAttribute("tabindex", "-1");
    try { box.focus({preventScroll: true}); } catch (e) { /* cosmetic */ }
  }

  /* ---------- messages ---------- */
  let liveEl = null;
  function live(text) { if (liveEl) { liveEl.textContent = ""; setTimeout(() => { liveEl.textContent = text || ""; }, 30); } }
  // a note under the buttons of the Selected card of `kind`, for the part `ref`
  function note(kind, ref, text) {
    X.msg[kind] = text ? {ref: ref, text: text} : null;
    live(text);
    const box = document.getElementById("sel-" + kind), m = box && box.querySelector(".xp-msg");
    if (m) { m.textContent = text || ""; m.hidden = !text; }
  }
  function netNote(text) {
    X.netMsg = text || "";
    live(text);
    const m = document.querySelector(".kt-card .xp-netmsg");
    if (m) { m.textContent = X.netMsg; m.hidden = !X.netMsg; }
  }

  /* ---------- the schematic: sheets and symbols ---------- */
  const projectOf = view => view && (view.project || (view.project = ctx.findProject(view.embed.shadowRoot || view.embed)));

  // every sheet instance (in the order of the sheet picker) and every symbol on it, by reference
  function schIndex() {
    const view = X.views.sch, project = projectOf(view);
    if (!project) return null;
    if (X.idx && X.idx.project === project) return X.idx;
    const order = ctx.D.sheets.map(s => s.replace(/^design\//, ""));
    const rank = p => {
      const f = String(p.filename || ""), i = order.findIndex(o => o === f || o.endsWith("/" + f) || f.endsWith("/" + o));
      return i < 0 ? 999 : i;
    };
    const pages = [...project.pages()].filter(p => p.type === "schematic" && p.document).sort((a, b) => rank(a) - rank(b));
    const byPath = new Map(pages.map(p => [p.sheet_path, p]));
    const root = project.root_schematic_page, rootDoc = root && root.document;
    const idx = {project: project, pages: [], refs: new Map()};
    pages.forEach((p, i) => {
      const parts = String(p.sheet_path || "").split("/").filter(Boolean), names = [];
      for (let k = 2; k <= parts.length; k++) {
        const q = byPath.get("/" + parts.slice(0, k).join("/"));
        names.push(q ? (q.name || "") : parts[k - 1]);
      }
      const name = p.name || String(p.filename || "").split("/").pop().replace(/\.kicad_sch$/, "");
      const info = {page: p, doc: p.document, i: i, name: name, label: `${rank(p) === 0 ? "Root" : i + 1} · ${name}`,
                    namePath: "/" + names.map(n => n + "/").join("")};
      idx.pages.push(info);
      const legacy = rootDoc && rootDoc.symbol_instances, own = p.document.symbol_instances;
      for (const sym of p.document.symbols.values()) {
        const key = `${p.sheet_path}/${sym.uuid}`;
        const inst = (legacy && legacy.get(key)) || (own && own.get(key)) || (sym.instances && sym.instances.get(p.sheet_path)) || null;
        const ref = (inst && inst.reference) || sym.reference;
        if (!ref || ref === "?") continue;
        const e = {info: info, sym: sym, ref: ref, unit: (inst && inst.unit) || sym.unit || 1};
        (idx.refs.get(ref) || idx.refs.set(ref, []).get(ref)).push(e);
      }
    });
    for (const list of idx.refs.values()) list.sort((a, b) => a.info.i - b.info.i || a.unit - b.unit);
    X.idx = idx;
    return idx;
  }
  const isPower = sym => { try { return !!(sym.lib_symbol && sym.lib_symbol.power); } catch (e) { return false; } };
  const isSelected = (v, item) => {
    const c = v.selected && v.selected.context;
    return !!c && (c === item || (c.uuid && c.uuid === item.uuid && c.reference === item.reference));
  };
  // select in the viewer without telling the sync mode (our own selections are not the user's)
  function pick(v, item) {
    X.busy++;
    try { v.select(item); } catch (e) { /* viewer reloading */ } finally { X.busy--; }
    return isSelected(v, item);
  }

  // show a sheet instance in the viewer: the sheet loads asynchronously and ends with its own zoom-to-page
  // and selection reset, so wait for that
  async function activate(page) {
    const view = X.views.sch, v = view.v, project = projectOf(view);
    if (project.active_page === page && v.document === page.document) return;
    await new Promise(done => {
      let t = null;
      const d = v.addEventListener("kicanvas:load", () => { clearTimeout(t); d.dispose(); done(); });
      t = setTimeout(() => { d.dispose(); done(); }, 6000);
      project.set_active_page(page);
    });
    await sleep(60);
  }
  async function selectSymbol(e, o) {
    const view = X.views.sch, v = view.v;
    if (!projectOf(view)) return false;
    await activate(e.info.page);
    let ok = false;
    for (let i = 0; i < 12 && !ok; i++) { ok = pick(v, e.sym); if (!ok) await sleep(150); }
    if (ok && o.zoom) { await settle(view); try { v.zoom_to_selection(); v.draw(); } catch (err) { /* ignore */ } }
    if (ok) {   // the viewer's own reset after a sheet change can land late
      await sleep(350);
      if (!isSelected(v, e.sym) && pick(v, e.sym) && o.zoom) { try { v.zoom_to_selection(); v.draw(); } catch (err) { /* ignore */ } }
    }
    return ok;
  }

  /* ---------- the board ---------- */
  const findFootprint = (v, ref) => ((v.document && v.document.footprints) || []).find(f => f.reference === ref) || null;
  async function selectFootprint(fp, o) {
    const view = X.views.pcb, v = view.v, cam = camOf(v);
    const bottom = fp.layer === "B.Cu";
    if (cam && bottom !== !!cam.flipped) {   // turn the board (the portal's Top / Bottom buttons keep their state)
      const b = document.querySelector(`[data-side="${bottom ? "bottom" : "top"}"]`);
      if (b) b.click(); else { try { v.flip_view(); } catch (e) { /* ignore */ } }
    }
    let ok = pick(v, fp);
    if (ok && o.zoom) {
      await settle(view);
      try { v.zoom_to_selection(); v.draw(); } catch (e) { /* ignore */ }
      await sleep(300);
      if (!isSelected(v, fp) && pick(v, fp)) { try { v.zoom_to_selection(); v.draw(); } catch (e) { /* ignore */ } }
    }
    return ok;
  }
  function padsOfNet(v, n) {
    const out = [];
    for (const fp of (v.document && v.document.footprints) || [])
      for (const pad of fp.pads || []) if (num(pad.net) === n) out.push({ref: fp.reference || "?", num: String(pad.number == null ? "" : pad.number)});
    return out;
  }
  // nets of a footprint: [{n, name}]
  function netsOf(v, ref) {
    const fp = findFootprint(v, ref), seen = new Map();
    for (const pad of (fp && fp.pads) || []) { const n = num(pad.net); if (n > 0 && pad.net.name) seen.set(n, pad.net.name); }
    return [...seen].map(([n, name]) => ({n: n, name: name})).filter(x => !x.name.startsWith("unconnected-"))
      .sort((a, b) => a.name.localeCompare(b.name, undefined, {numeric: true}));
  }

  /* ---------- selections: what the Selected card shows ---------- */
  // the symbol (or footprint) of a selected item: a field of a symbol counts as its symbol
  function ownerOf(item) {
    if (!item) return null;
    if (item.reference && item.reference !== "?") return {part: item};
    const p = item.parent;
    return p && p.reference && p.reference !== "?" ? {part: p} : null;
  }
  function fitted(ref) {
    const vp = window.KHVariants && KHVariants.part(ref);
    if (vp) return vp.notFitted ? `Not fitted in ${vp.variant}: its footprint stays on the board, drawn as DNP.` : "";
    const p = ctx.D.parts && ctx.D.parts[ref];
    return p && p.dnp ? "Not fitted (DNP): its footprint stays on the board, drawn as DNP." : "";
  }
  const whyNoFootprint = (sym, ref) => {
    if (isPower(sym) || /^#/.test(ref)) return `${ref} is a power symbol: it has no footprint on the board.`;
    if (sym.on_board === false) return `${ref} is excluded from the board in the schematic: it has no footprint.`;
    return "";
  };

  // called by portal.html's showPart after it drew the card
  function decorate(kind, box, item) {
    if (!X.both || !X.views[kind]) { return; }
    const o = ownerOf(item);
    if (!o) { X.cur[kind] = null; return; }
    const ref = o.part.reference;
    X.cur[kind] = {ref: ref, part: o.part};
    let html = "";
    if (kind === "pcb") {
      html = `<button class="btn xp-btn" type="button" data-xp="part-sch" data-ref="${esc(ref)}" title="Open the schematic on this part">Show in schematic</button>`;
    } else {
      html = `<button class="btn xp-btn" type="button" data-xp="part-pcb" data-ref="${esc(ref)}" title="Open the board on this footprint">Show on PCB</button>`;
      const idx = schIndex(), list = idx && idx.refs.get(ref);
      if (list && list.length > 1) {
        const here = list.findIndex(e => e.sym === o.part);
        html += `<div class="xp-units muted">Units: ` + list.map((e, k) => k === here ? `<b>${letter(e.unit)}</b>` :
          `<button type="button" class="netbtn" data-xp="unit" data-ref="${esc(ref)}" data-k="${k}" title="Open unit ${letter(e.unit)} on ${esc(e.info.label)}">` +
          `${letter(e.unit)} · ${esc(e.info.name)}</button>`).join(" ") + `</div>`;
      }
      const pv = ctx.viewers.pcb;
      if (pv && pv.document) {
        const nets = netsOf(pv, ref).slice(0, 12);
        if (nets.length) html += `<div class="xp-nets"><span class="muted">Nets on the PCB:</span> ` + nets.map(x =>
          `<button type="button" class="netbtn" data-xp="net-pcb" data-n="${x.n}" title="Highlight this net on the board">${esc(x.name)}</button>`).join(" ") + `</div>`;
      }
    }
    const m = X.msg[kind] && X.msg[kind].ref === ref ? X.msg[kind].text : "";
    const div = document.createElement("div");
    div.className = "xp-box";
    div.innerHTML = html + `<div class="xp-msg muted" ${m ? "" : "hidden"}>${esc(m)}</div>`;
    // right under the value, so the buttons are in view above the long list of nets and offers
    const anchor = box.querySelector(".sel-val");
    if (anchor) anchor.insertAdjacentElement("afterend", div); else box.appendChild(div);
  }

  /* ---------- part: PCB -> schematic ---------- */
  // o.zoom: zoom to it; o.background: only select what is cheap to select, no tab switch (sync mode)
  async function partToSch(ref, o) {
    o = o || {};
    const from = shown();
    let v = ctx.viewers.sch;
    if (!o.background) v = await openTab("sch");
    else if (!v || !v.document || !projectOf(X.views.sch)) return false;
    if (!v) { note("pcb", ref, "The schematic is still loading: try again in a moment."); return false; }
    const idx = schIndex(), list = idx && idx.refs.get(ref);
    if (!list || !list.length) {
      note("pcb", ref, `${ref} is not in the schematic (the board may be out of date: update the PCB from the schematic).`);
      if (!o.background && from && from !== "sch" && !o.arrive) location.hash = TAB[from];
      return false;
    }
    const e = list[0];   // the first unit of the first sheet
    if (o.background && projectOf(X.views.sch).active_page !== e.info.page) return false;   // lands on it when the tab opens
    note("sch", ref, `On sheet ${e.info.label}.` + (list.length > 1 ? ` Unit ${letter(e.unit)} of ${list.length} (the first one), the others are listed below.` : ""));
    const ok = await selectSymbol(e, {zoom: !o.background});
    if (ok && o.focus) focusCard("sch");
    return ok;
  }

  /* ---------- part: schematic -> PCB ---------- */
  async function partToPcb(ref, o) {
    o = o || {};
    const cur = X.cur.sch, sym = cur && cur.ref === ref ? cur.part : null;
    const from = shown();
    const why = sym && whyNoFootprint(sym, ref);
    if (why) { note("sch", ref, why); return false; }
    let v = ctx.viewers.pcb;
    if (!o.background) v = await openTab("pcb");
    else if (!v || !v.document) return false;
    if (!v) { note("sch", ref, "The board is still loading: try again in a moment."); return false; }
    const fp = findFootprint(v, ref);
    if (!fp) {
      note("sch", ref, sym && !sym.footprint ? `${ref} has no footprint assigned in the schematic.` :
        `${ref} is not on the board: no footprint with that reference (update the PCB from the schematic).`);
      if (!o.background && from && from !== "pcb" && !o.arrive) location.hash = TAB[from];
      return false;
    }
    note("pcb", ref, [`On the ${fp.layer === "B.Cu" ? "bottom" : "top"} side.`, fitted(ref)].filter(Boolean).join(" "));
    const ok = await selectFootprint(fp, {zoom: !o.background});
    if (ok && o.focus) focusCard("pcb");
    return ok;
  }

  /* ---------- net: schematic marks ---------- */
  // pin position on its sheet, as KiCanvas's pin painter transforms it (rotate, mirror, translate; y flips)
  function pinPos(sym, pin) {
    const def = pin.definition, p = def.at.position;
    let x = p.x, y = p.y;
    const rot = (((sym.at.rotation || 0) % 360) + 360) % 360;
    for (let i = 0; i < Math.round(rot / 90); i++) [x, y] = [-y, x];
    if (sym.mirror === "x") y = -y;
    else if (sym.mirror === "y") x = -x;
    return {x: sym.at.position.x + x, y: sym.at.position.y - y};
  }
  function computeMarks(idx, name, pads) {
    const per = new Map(idx.pages.map(i => [i, []]));
    const seen = new Set();
    const add = (info, x, y, k, text) => {
      if (!Number.isFinite(x) || !Number.isFinite(y)) return;
      const key = info.i + "|" + k + "|" + x.toFixed(2) + "|" + y.toFixed(2);
      if (seen.has(key)) return;
      seen.add(key); per.get(info).push({x: x, y: y, k: k, text: text});
    };
    // connected pins, from the pads of the net on the board
    for (const pad of pads) for (const e of idx.refs.get(pad.ref) || []) {
      try {
        for (const pin of e.sym.unit_pins) if (String(pin.number) === pad.num) { const q = pinPos(e.sym, pin); add(e.info, q.x, q.y, "pin", `${pad.ref} pin ${pad.num}`); }
      } catch (err) { /* a symbol without that pin in its library */ }
    }
    // labels and power symbols of the net, by name
    const cut = name.lastIndexOf("/"), dir = name.slice(0, cut + 1), base = name.slice(cut + 1);
    for (const info of idx.pages) {
      const d = info.doc;
      for (const l of d.global_labels || []) if (l.text === name) add(info, l.at.position.x, l.at.position.y, "label", l.text);
      for (const l of d.net_labels || []) if (info.namePath + l.text === name) add(info, l.at.position.x, l.at.position.y, "label", l.text);
      for (const l of d.hierarchical_labels || [])   // a hierarchical label carries the net down from its sheet pin
        if (info.namePath + l.text === name || (l.text === base && info.namePath.startsWith(dir))) add(info, l.at.position.x, l.at.position.y, "label", l.text);
      for (const s of d.symbols.values()) if (isPower(s) && s.value === name) add(info, s.at.position.x, s.at.position.y, "label", s.value);
    }
    return idx.pages.map(i => ({info: i, items: per.get(i).slice(0, MAX_MARKS)})).filter(p => p.items.length);
  }
  const countOf = (items, k) => items.filter(x => x.k === k).length;

  /* ---------- net: the viewer's own connectivity (the KiCanvas fork) ---------- */
  const native = () => {
    const v = ctx.viewers.sch;
    try { return !!(v && typeof v.highlight_net === "function" && v.document && v.netlist) && v; } catch (e) { return false; }
  };
  // what a net has on one sheet, as points: pins (the end that wires attach to), labels and power symbols,
  // and the wires, junctions and sheet pins, which only count for framing the net
  function netItems(sv, snet, path) {
    const out = [], nl = sv.netlist;
    const at = (it, k, text) => { const p = it.at && it.at.position; if (p) out.push({x: p.x, y: p.y, k: k, text: text}); };
    for (const it of snet.items_on(path)) {
      if (it.pts) for (const p of it.pts) out.push({x: p.x, y: p.y, k: "wire"});
      else if (typeof it.number === "string" && it.parent) {
        const s = nl.pin_segment(path, it);
        if (s) out.push({x: s.a.x, y: s.a.y, k: "pin", text: `${it.parent.reference} pin ${it.number}`});
      }
      else if (it.lib_symbol) at(it, "label", it.value);
      else if (typeof it.shown_text === "string") at(it, "label", it.shown_text);
      else at(it, "wire");
    }
    return out;
  }
  // the sheet instances a net is drawn on, in the order of the sheet picker
  function nativePages(idx, sv, snet) {
    const paths = new Set(snet.sheet_paths), nl = sv.netlist;
    // the project's pages may not be the netlist's sheet instances (files loaded one by one): go by the document
    const pathOf = i => [i.page.sheet_path, ...nl.paths_of(i.doc)].find(p => paths.has(p));
    return idx.pages.map(i => ({info: i, path: pathOf(i)})).filter(p => p.path)
      .map(p => ({info: p.info, items: netItems(sv, snet, p.path)})).filter(p => p.items.length);
  }
  // the schematic net of a net of the board: the one most of its pads' pins are on, else the one named alike
  function snetOfPcb(sv, pv, net) {
    const votes = new Map();
    for (const pad of padsOfNet(pv, net.net)) {
      let n = null;
      try { n = sv.netlist.net_for_pin(pad.ref, pad.num); } catch (e) { /* a symbol without a library symbol */ }
      if (n) votes.set(n, (votes.get(n) || 0) + 1);
    }
    let best = null, count = 0;
    for (const [n, c] of votes) if (c > count) { best = n; count = c; }
    return best || sv.netlist.net_by_name(net.name) || null;
  }
  // the net of the board of a schematic net: by the footprints and pads of its pins, else by its name
  function pcbNetOf(snet) {
    const pv = ctx.viewers.pcb;
    if (!pv || !pv.document) return null;
    const votes = new Map();
    for (const p of snet.pins || []) {
      const fp = findFootprint(pv, p.reference);
      for (const pad of (fp && fp.pads) || []) {
        const n = String(pad.number) === p.number ? num(pad.net) : null;
        if (n > 0) votes.set(n, (votes.get(n) || 0) + 1);
      }
    }
    let best = null, count = 0;
    for (const [n, c] of votes) if (c > count) { best = n; count = c; }
    if (best != null) return best;
    for (const fp of pv.document.footprints || [])
      for (const pad of fp.pads || []) if (pad.net && pad.net.name === snet.name && num(pad.net) > 0) return num(pad.net);
    return null;
  }
  // a note in the Net card (the Selected card has none when a wire is picked)
  function cardNote(text) {
    const view = X.views.sch, m = view && view.card && view.card.querySelector(".xp-cmsg");
    live(text);
    if (m) { m.textContent = text || ""; m.hidden = !text; }
  }
  // highlight in the viewer without taking our own call for the user's pick
  function highlightNative(sv, snet) {
    X.busy++;
    try { sv.highlight_net(snet); } finally { X.busy--; }
  }

  async function showMarkSheet(i, zoom) {
    const m = X.marks; if (!m) return;
    m.cur = (i + m.pages.length) % m.pages.length;
    const p = m.pages[m.cur], view = X.views.sch, v = view.v;
    cardNote("");
    await activate(p.info.page);
    if (m.native && X.marks === m) highlightNative(view.v, m.native);   // the viewer keeps it across sheets: this is for the first one
    X.markId++; view.rev++;
    renderCard();
    if (zoom) {
      await settle(view);
      const fit = () => {
        const cam = camOf(v);
        let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
        for (const q of p.items) { x0 = Math.min(x0, q.x); y0 = Math.min(y0, q.y); x1 = Math.max(x1, q.x); y1 = Math.max(y1, q.y); }
        const w = Math.max(x1 - x0, 24), h = Math.max(y1 - y0, 24), mg = Math.max(w, h) * 0.2;
        if (cam) { cam.bbox = {x: (x0 + x1) / 2 - w / 2 - mg, y: (y0 + y1) / 2 - h / 2 - mg, w: w + 2 * mg, h: h + 2 * mg}; v.draw(); }
      };
      fit();
      await sleep(350);   // the sheet's own reset after loading can land late
      if (X.marks === m && X.marks.pages[X.marks.cur] === p) fit();
    }
  }
  async function netToSch(net) {
    const from = shown();
    const v = await openTab("sch");
    if (!v) { netNote("The schematic is still loading: try again in a moment."); return; }
    const idx = schIndex(), pv = ctx.viewers.pcb, sv = native();
    let snet = null, pages = [];
    if (idx && pv && sv) {
      try { snet = snetOfPcb(sv, pv, net); } catch (e) { snet = null; }
      if (snet) pages = nativePages(idx, sv, snet);
    }
    if (!pages.length) {   // no connectivity in the viewer, or the net is not in the schematic: rings by name
      snet = null;
      pages = idx && pv ? computeMarks(idx, net.name, padsOfNet(pv, net.net)) : [];
    }
    if (!pages.length) {
      netNote(`${net.name}: no label or connected pin found in the schematic.`);
      if (from && from !== "sch") location.hash = TAB[from];
      return;
    }
    netNote("");
    const act = projectOf(X.views.sch).active_page;
    X.marks = {net: net, pages: pages, cur: 0, native: snet};
    const here = pages.findIndex(p => p.info.page === act);
    await showMarkSheet(here >= 0 ? here : 0, true);
    focusCard("sch");
  }
  function clearMarks() {
    if (!X.marks) return;
    const was = X.marks;
    X.marks = null; X.markId++;
    const view = X.views.sch; if (view) view.rev++;
    if (was.native && view) highlightNative(view.v, null);
    renderCard();
  }
  const placing = () => !!((window.KHPins && KHPins.active && KHPins.active()) || (window.KHTools && KHTools.measuring && KHTools.measuring()));
  // the viewer's net highlight changed (a click on a wire, label or pin; or a call of ours, which we skip)
  function onSchNet(snet) {
    if (X.busy) return;
    const view = X.views.sch;
    if (!view) return;
    // Comment and Measure use the click for their own purpose: undo the viewer's pick of a net
    if (snet && placing()) { X.busy++; try { view.v.highlight_net(null); } finally { X.busy--; } return; }
    if (!snet) {
      if (X.marks && X.marks.native) { X.marks = null; X.markId++; view.rev++; renderCard(); }
      return;
    }
    const idx = schIndex(), sv = native();
    if (!idx || !sv) return;
    const pages = nativePages(idx, sv, snet), act = projectOf(view).active_page;
    X.marks = {net: {net: null, name: snet.name}, pages: pages, cur: Math.max(0, pages.findIndex(p => p.info.page === act)), native: snet};
    X.markId++; view.rev++;
    cardNote("");
    renderCard();
    X.lastNet = {kind: "sch", snet: snet, seq: ++X.seq};
    live(`Net ${snet.name} highlighted.`);
  }
  async function netToPcb(n) {
    const from = shown();
    const v = await openTab("pcb");
    if (!v) { note("sch", (X.cur.sch || {}).ref, "The board is still loading: try again in a moment."); return false; }
    X.busy++;   // the highlight is ours, not a pick of the user's
    let ok;
    try { ok = window.KHTools && KHTools.showNet ? KHTools.showNet(n) : (v.highlight_net(n), true); } finally { X.busy--; }
    if (!ok) { if (X.marks) cardNote("That net has nothing on the board to highlight."); else note("sch", (X.cur.sch || {}).ref, "That net has nothing on the board to highlight."); if (from && from !== "pcb") location.hash = TAB[from]; return false; }
    return true;
  }

  /* ---------- cards ---------- */
  function buildSchCard(view) {
    const sel = document.getElementById("sel-sch");
    if (!sel) return;
    const card = document.createElement("div");
    card.className = "card side xp-card"; card.hidden = true;
    card.innerHTML = `<h3>Net in the schematic</h3>
      <div class="xp-name"></div><div class="xp-stat muted" role="status" aria-live="polite"></div>
      <div class="xp-row">
        <button class="btn" type="button" data-xp="prev-sheet" aria-label="Previous sheet with the net">&larr; Prev sheet</button>
        <button class="btn" type="button" data-xp="next-sheet" aria-label="Next sheet with the net">Next sheet &rarr;</button>
        <button class="btn" type="button" data-xp="marks-pcb">Show on PCB</button>
        <button class="btn" type="button" data-xp="marks-clear">Clear</button>
      </div>
      <div class="muted xp-cmsg" role="status" hidden></div>
      <div class="muted xp-hint"></div>`;
    sel.insertAdjacentElement("beforebegin", card);   // above the long Selected card, in view
    view.card = card;
  }
  function renderCard() {
    const view = X.views.sch, m = X.marks;
    if (!view || !view.card) return;
    view.card.hidden = !m;
    if (!m) return;
    const p = m.pages[m.cur];
    view.card.querySelector(".xp-name").textContent = m.net.name;
    view.card.querySelector(".xp-hint").textContent = m.native ?
      "Highlighted on the sheet and on the others it is drawn on. Click another wire, label or pin to pick another net. Esc clears." :
      "Rings: connected pins (yellow) and labels or power symbols of the net (orange). Esc clears.";
    view.card.querySelector(".xp-stat").textContent = !p ? "Not drawn on any sheet." : `Sheet ${m.cur + 1} of ${m.pages.length} · ${p.info.label} · ` +
      [countOf(p.items, "pin") ? plural(countOf(p.items, "pin"), "pin") : "", countOf(p.items, "label") ? plural(countOf(p.items, "label"), "label") : ""].filter(Boolean).join(", ");
    for (const b of view.card.querySelectorAll('[data-xp="prev-sheet"],[data-xp="next-sheet"]')) b.disabled = m.pages.length < 2;
  }
  // the PCB's Nets card (viewer-tools.js) gets its button when a net is highlighted
  function netButton(detail) {
    X.pcbNet = detail && detail.net != null ? detail : null;
    if (X.pcbNet && !X.busy) X.lastNet = {kind: "pcb", net: X.pcbNet, seq: ++X.seq};
    const card = document.querySelector(".kt-card"), row = card && card.querySelector(".kt-row");
    if (!row) return;
    let b = row.querySelector('[data-xp="net-sch"]');
    if (!b) {
      b = document.createElement("button");
      b.className = "btn"; b.type = "button"; b.dataset.xp = "net-sch";
      b.textContent = "Show in schematic"; b.title = "Open the schematic on the sheets where this net is";
      row.insertBefore(b, row.firstChild);
      const m = document.createElement("div");
      m.className = "kt-pad xp-netmsg muted"; m.setAttribute("role", "status"); m.hidden = true;
      row.insertAdjacentElement("afterend", m);
    }
    b.hidden = !X.pcbNet;
    const m = card.querySelector(".xp-netmsg");
    if (m) { m.textContent = ""; m.hidden = true; }
    X.netMsg = "";
  }

  /* ---------- sync mode ---------- */
  function setSync(on) {
    X.sync = !!on;
    try {
      const u = new URL(location.href);
      if (X.sync) u.searchParams.set(PARAM, "1"); else u.searchParams.delete(PARAM);
      history.replaceState(history.state, "", u.pathname + u.search + u.hash);
    } catch (e) { /* the toggle still works without the link */ }
    for (const view of Object.values(X.views)) {
      view.sbtn.classList.toggle("on", X.sync);
      view.sbtn.setAttribute("aria-pressed", X.sync ? "true" : "false");
    }
    live(X.sync ? "Sync on: selecting a part in one tab selects it in the other." : "Sync off.");
    const here = shown();
    if (X.sync && X.last && here && X.last.kind === here && !(X.lastNet && X.lastNet.seq > X.last.seq)) syncTo(other(here), X.last.ref);
  }
  async function syncTo(kind, ref) {
    if (/^#/.test(ref) || !X.views[kind]) return;
    try { kind === "pcb" ? await partToPcb(ref, {background: true}) : await partToSch(ref, {background: true}); }
    catch (e) { /* the other tab is not ready: it lands on the part when opened */ }
  }
  // a tab opened while sync is on: land on the net last picked in the other one, if it is the newer pick
  async function arriveNet(kind) {
    const L = X.lastNet;
    if (!L || L.kind === kind) return false;
    if (kind === "pcb") {
      const n = L.snet ? pcbNetOf(L.snet) : null;
      if (n == null) return false;
      return netToPcb(n);
    }
    const net = L.net;
    if (!net) return false;
    await netToSch(net);
    return true;
  }
  // a tab opened while sync is on: land on the part (or net) last picked in the other one
  async function arrive() {
    if (!X.sync) return;
    const kind = shown();
    if (!kind) return;
    if (X.lastNet && X.lastNet.kind !== kind && (!X.last || X.lastNet.seq > X.last.seq)) {
      await sleep(50);
      if (!ctx.viewers[kind]) { if (!(await openTab(kind))) return; }
      await arriveNet(kind);
      return;
    }
    if (!X.last || X.last.kind === kind) return;
    const ref = X.last.ref;
    await sleep(50);
    if (!ctx.viewers[kind]) { if (!(await openTab(kind))) return; }
    if (X.last.ref !== ref || shown() !== kind) return;
    kind === "pcb" ? await partToPcb(ref, {arrive: true}) : await partToSch(ref, {arrive: true});
  }

  function onSelect(kind, item) {
    if (X.busy) return;
    const o = ownerOf(item);
    if (!o) return;
    X.last = {kind: kind, ref: o.part.reference, seq: ++X.seq};
    if (X.sync) syncTo(other(kind), o.part.reference);
  }

  /* ---------- attaching to a viewer ---------- */
  function attach(kind, v) {
    if (X.views[kind] || !ctx || !X.both) return;
    const embed = document.getElementById("kc-" + kind);
    const host = embed && embed.closest(".viewer");
    const bar = host && host.querySelector(".vbar");
    if (!host || !bar || !v.canvas) return;
    const view = {kind: kind, v: v, embed: embed, host: host, project: null, rev: 0, key: null};
    X.views[kind] = view;

    view.sbtn = document.createElement("button");
    view.sbtn.className = "btn" + (X.sync ? " on" : ""); view.sbtn.type = "button"; view.sbtn.textContent = "Sync";
    view.sbtn.title = "Sync the tabs: selecting a part in one selects it in the other";
    view.sbtn.setAttribute("aria-pressed", X.sync ? "true" : "false");
    view.sbtn.addEventListener("click", () => setSync(!X.sync));
    bar.insertBefore(view.sbtn, bar.querySelector(".hint"));

    v.addEventListener("kicanvas:select", e => onSelect(kind, e.detail && e.detail.item));
    if (kind === "sch") v.addEventListener("kicanvas:nethighlight", e => onSchNet(e.detail && e.detail.net));

    if (kind === "sch") {
      view.layer = document.createElement("div");
      view.layer.className = "xp-layer";
      view.svg = el("svg", {"aria-hidden": "true"});
      view.svg.setAttribute("class", "xp-svg");
      view.layer.appendChild(view.svg);
      host.appendChild(view.layer);
      buildSchCard(view);
    }
    if (ctx.reshow) ctx.reshow();   // the Selected cards get their buttons
  }

  /* ---------- drawing over the schematic ---------- */
  function render(view) {
    const v = view.v, cam = camOf(v);
    if (!cam || !view.embed.offsetParent) return;
    const m = X.marks, project = projectOf(view);
    const cur = m && !m.native && m.pages[m.cur];
    const on = !!(cur && project && project.active_page === cur.info.page && v.document === cur.info.doc);
    const cr = v.canvas.getBoundingClientRect(), hr = view.host.getBoundingClientRect();
    const key = [cam.center.x, cam.center.y, cam.zoom, cam.viewport_size.x, cam.viewport_size.y,
                 cr.left - hr.left, cr.top - hr.top, cr.width, cr.height, view.rev, on ? X.markId : -1].join();
    if (key === view.key) return;
    view.key = key;
    const L = view.layer.style;
    L.left = (cr.left - hr.left - view.host.clientLeft) + "px"; L.top = (cr.top - hr.top - view.host.clientTop) + "px";
    L.width = cr.width + "px"; L.height = cr.height + "px";
    const svg = view.svg;
    svg.setAttribute("width", cr.width); svg.setAttribute("height", cr.height);
    svg.textContent = "";
    if (!on) return;
    for (const q of cur.items) {
      const s = cam.world_to_screen({x: q.x, y: q.y});
      if (s.x < -20 || s.y < -20 || s.x > cr.width + 20 || s.y > cr.height + 20) continue;
      const r = q.k === "label" ? 12 : 7;
      el("circle", {cx: s.x, cy: s.y, r: r, class: "xp-halo"}, svg);
      el("circle", {cx: s.x, cy: s.y, r: r, class: q.k === "label" ? "xp-label" : "xp-pin"}, svg);
    }
  }
  function tick() {
    const view = X.views.sch;
    if (view) { try { render(view); } catch (e) { /* viewer reloading */ } }
    requestAnimationFrame(tick);
  }

  /* ---------- clicks, keys, styles ---------- */
  function onClick(e) {
    const b = e.target.closest && e.target.closest("[data-xp]");
    if (!b || b.disabled) return;
    const k = b.dataset.xp;
    if (X.working) return;
    const run = async fn => { X.working = true; try { await fn(); } catch (err) { /* the viewer was busy: pressing again works */ } finally { X.working = false; } };
    if (k === "part-sch") run(() => partToSch(b.dataset.ref, {zoom: true, focus: true}));
    else if (k === "part-pcb") run(() => partToPcb(b.dataset.ref, {zoom: true, focus: true}));
    else if (k === "net-pcb") run(() => netToPcb(+b.dataset.n));
    else if (k === "net-sch") { if (X.pcbNet) run(() => netToSch(X.pcbNet)); }
    else if (k === "prev-sheet") run(() => showMarkSheet(X.marks.cur - 1, true));
    else if (k === "next-sheet") run(() => showMarkSheet(X.marks.cur + 1, true));
    else if (k === "marks-pcb") {
      if (!X.marks) return;
      const n = X.marks.native ? pcbNetOf(X.marks.native) : X.marks.net.net;
      if (n == null) cardNote("That net is not on the board (no pad of its pins carries it).");
      else run(() => netToPcb(n));
    }
    else if (k === "marks-clear") clearMarks();
    else if (k === "unit") {
      const list = (schIndex() || {refs: new Map()}).refs.get(b.dataset.ref), en = list && list[+b.dataset.k];
      if (en) run(async () => { note("sch", b.dataset.ref, `Unit ${letter(en.unit)} on sheet ${en.info.label}.`); await selectSymbol(en, {zoom: true}); });
    }
  }
  function injectStyle() {
    const s = document.createElement("style");
    s.textContent = `
.viewer { position: relative; }
.xp-layer { position: absolute; overflow: hidden; pointer-events: none; z-index: 5; }
.xp-svg { position: absolute; left: 0; top: 0; overflow: hidden; }
.xp-halo { fill: none; stroke: rgba(0,0,0,.65); stroke-width: 5; }
.xp-pin { fill: rgba(255,212,0,.15); stroke: #ffd400; stroke-width: 2; }
.xp-label { fill: none; stroke: var(--brand, #e8622c); stroke-width: 3; stroke-dasharray: 5 3; }
.xp-box { margin: 6px 0 10px; }
.xp-box .xp-btn { margin: 0 4px 4px 0; font-size: 12px; padding: 3px 9px; }
.xp-msg, .xp-units, .xp-nets, .xp-netmsg, .xp-hint, .xp-cmsg { font-size: 12px; margin-top: 6px; overflow-wrap: anywhere; }
.xp-units b { font-weight: 700; color: var(--fg); }
.xp-name { font-weight: 700; overflow-wrap: anywhere; }
.xp-stat { font-size: 12px; margin-bottom: 8px; }
.xp-row { display: flex; gap: 6px; flex-wrap: wrap; }
.xp-row .btn { padding: 3px 9px; font-size: 12px; }
.xp-row .btn:disabled { opacity: .5; cursor: default; }
#sel-pcb:focus-visible, #sel-sch:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
`;
    document.head.appendChild(s);
  }

  window.KHCross = {
    init: function (c) {
      if (X.started) return;
      ctx = c; X.started = true;
      if (!(c.D.sheets || []).length || !c.D.pcb) return;   // nothing to cross between
      X.both = true;
      try { X.sync = new URLSearchParams(location.search).get(PARAM) === "1"; } catch (e) { X.sync = false; }
      injectStyle();
      liveEl = document.createElement("div");
      liveEl.setAttribute("role", "status"); liveEl.setAttribute("aria-live", "polite");
      liveEl.style.cssText = "position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap";
      document.body.appendChild(liveEl);
      document.addEventListener("click", onClick);
      document.addEventListener("kh:net", e => netButton(e.detail));
      document.addEventListener("keydown", e => { if (e.key === "Escape" && X.marks) clearMarks(); });
      window.addEventListener("hashchange", () => { arrive().catch(() => {}); });
      document.addEventListener("kh:viewer", e => attach(e.detail.kind, e.detail.viewer));
      for (const [kind, v] of Object.entries(c.viewers)) attach(kind, v);
      requestAnimationFrame(tick);
    },
    decorate: decorate,
  };
})();
