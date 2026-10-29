/* Interactive compare of two versions of a page, shared by portal.html (the "Changes" tab
   of a pull request) and compare.html (any two builds). Published as assets/compare.js.

   Markup (the page builds it, see both templates):
     <div class="cmp" data-mode="swipe|fade|side|diff">
       <div class="cmp-bar"> buttons[data-mode], input[type=range], button[data-fit] </div>
       <div class="cmp-view"><div class="cmp-stage"> <img class="a"> <img class="b"> <img class="d" hidden> </div>
         <div class="cmp-line"></div> <span class="cmp-tag">…</span> <span class="cmp-tag">…</span></div></div>

   KHCompare.mount(root)       wires one .cmp: Swipe / Fade / Side by side / Changes, wheel
                               zoom around the cursor, drag to pan
   KHCompare.prepare(a, b)     for renders of two builds: both images on one white canvas
                               (so different page sizes line up) and the red/green overlay
                               computed here, in the browser, like scripts/visual_diff.py:
                               a pixel changes when a channel differs by more than THRESHOLD;
                               red = ink only in A, green = ink only in B, grey = unchanged.
                               Resolves to {a, b, d, w, h, changed} (image URLs, changed pixels) */
(function () {
  const CSS = `
  .cmp { margin: 6px 0 22px; }
  .cmp-bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 6px 0; }
  .cmp-bar input[type=range] { flex: 1; min-width: 120px; max-width: 320px; }
  .cmp-view { position: relative; height: 62vh; overflow: hidden; background: #fff; border: 1px solid var(--line);
              border-radius: 8px; cursor: grab; touch-action: none; }
  .cmp-view.drag { cursor: grabbing; }
  .cmp-stage { position: absolute; left: 0; top: 0; transform-origin: 0 0; }
  .cmp-stage img { position: absolute; left: 0; top: 0; max-width: none !important; border: 0 !important;
                   border-radius: 0 !important; user-select: none; -webkit-user-drag: none; }
  .cmp-line { position: absolute; top: 0; bottom: 0; width: 2px; margin-left: -1px; background: var(--brand); pointer-events: none; }
  .cmp-tag { position: absolute; top: 8px; font-size: 12px; background: rgba(0,0,0,.65); color: #fff; padding: 2px 8px;
             border-radius: 10px; pointer-events: none; max-width: 45%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }`;
  const style = document.createElement("style");
  style.textContent = CSS;
  document.head.appendChild(style);

  const THRESHOLD = 48;   // per-channel difference that counts as a change (visual_diff.py THRESHOLD)
  const INK = 230;        // a pixel darker than this in some channel is drawing, not paper

  const loadImage = url => new Promise((ok, fail) => {
    const im = new Image();
    im.onload = () => ok(im);
    im.onerror = () => fail(new Error("Could not load " + url));
    im.src = url;
  });
  const blobUrl = canvas => new Promise(ok => canvas.toBlob(b => ok(URL.createObjectURL(b)), "image/png"));

  async function prepare(urlA, urlB) {
    const [ia, ib] = await Promise.all([urlA ? loadImage(urlA) : null, urlB ? loadImage(urlB) : null]);
    const W = Math.max(ia ? ia.naturalWidth : 0, ib ? ib.naturalWidth : 0);
    const H = Math.max(ia ? ia.naturalHeight : 0, ib ? ib.naturalHeight : 0);
    const draw = im => {
      const c = document.createElement("canvas");
      c.width = W; c.height = H;
      const g = c.getContext("2d", {willReadFrequently: true});
      g.fillStyle = "#fff"; g.fillRect(0, 0, W, H);
      if (im) g.drawImage(im, 0, 0);
      return c;
    };
    const ca = draw(ia), cb = draw(ib);
    const a = ca.getContext("2d").getImageData(0, 0, W, H).data, b = cb.getContext("2d").getImageData(0, 0, W, H).data;
    const out = new ImageData(W, H), o = out.data;
    let changed = 0;
    for (let i = 0; i < a.length; i += 4) {
      const inkA = Math.min(a[i], a[i + 1], a[i + 2]) < INK, inkB = Math.min(b[i], b[i + 1], b[i + 2]) < INK;
      const diff = Math.abs(a[i] - b[i]) > THRESHOLD || Math.abs(a[i + 1] - b[i + 1]) > THRESHOLD || Math.abs(a[i + 2] - b[i + 2]) > THRESHOLD;
      let r = 255, g = 255, bl = 255;
      if (inkA || inkB) { r = g = bl = 190; }
      if (diff) {
        changed++;
        if (inkA) { r = 220; g = 30; bl = 30; }
        if (inkB) { r = 0; g = 150; bl = 60; }  // added wins where both have ink, as in visual_diff.py
      }
      o[i] = r; o[i + 1] = g; o[i + 2] = bl; o[i + 3] = 255;
    }
    const cd = document.createElement("canvas");
    cd.width = W; cd.height = H;
    cd.getContext("2d").putImageData(out, 0, 0);
    // originals are used as they are when they already have the common size
    const fit = (im, url, c) => im && im.naturalWidth === W && im.naturalHeight === H ? url : blobUrl(c);
    const [ua, ub, ud] = await Promise.all([fit(ia, urlA, ca), fit(ib, urlB, cb), blobUrl(cd)]);
    return {a: ua, b: ub, d: ud, w: W, h: H, changed};
  }

  /* Both versions of a page stacked; swipe, fade, side by side or the red/green overlay;
     wheel zooms around the cursor, drag pans. */
  function mount(root) {
    const view = root.querySelector(".cmp-view"), stage = root.querySelector(".cmp-stage");
    const A = root.querySelector("img.a"), B = root.querySelector("img.b"), Dif = root.querySelector("img.d");
    const line = root.querySelector(".cmp-line"), range = root.querySelector("input[type=range]");
    const tags = root.querySelectorAll(".cmp-tag");
    let mode = root.dataset.mode || "swipe", W = 0, H = 0, s = 1, x = 0, y = 0;
    const GAP = 24;
    const apply = () => {
      stage.style.transform = `translate(${x}px, ${y}px) scale(${s})`;
      const pct = +range.value;
      B.style.left = mode === "side" ? `${W + GAP}px` : "0";
      B.style.clipPath = mode === "swipe" ? `inset(0 0 0 ${pct}%)` : "none";
      B.style.opacity = mode === "fade" ? pct / 100 : 1;
      A.hidden = mode === "diff"; B.hidden = mode === "diff"; Dif.hidden = mode !== "diff";
      range.hidden = !(mode === "swipe" || mode === "fade");
      line.hidden = mode !== "swipe";
      line.style.left = `${x + s * W * pct / 100}px`;
      tags.forEach(t => { t.hidden = mode === "diff"; });
    };
    const fit = () => {
      if (!view.clientWidth || !W) { requestAnimationFrame(fit); return; }  // tab not laid out yet
      const vw = view.clientWidth, vh = view.clientHeight, w = mode === "side" ? 2 * W + GAP : W;
      s = Math.min(vw / w, vh / H) * 0.98;
      x = (vw - w * s) / 2; y = (vh - H * s) / 2;
      apply();
    };
    const ready = () => {
      W = A.naturalWidth; H = A.naturalHeight;
      [A, B, Dif].forEach(im => { im.style.width = W + "px"; });  // the overlay may be larger: same box
      fit();
    };
    root.querySelectorAll("[data-mode]").forEach(o => o.classList.toggle("on", o.dataset.mode === mode));
    if (A.complete && A.naturalWidth) ready(); else A.addEventListener("load", ready, {once: true});
    root.querySelector(".cmp-bar").addEventListener("click", e => {
      const b = e.target.closest("button"); if (!b) return;
      if (b.dataset.mode) {
        mode = b.dataset.mode;
        root.querySelectorAll("[data-mode]").forEach(o => o.classList.toggle("on", o === b));
        fit();
      } else if (b.hasAttribute("data-fit")) fit();
    });
    range.addEventListener("input", apply);
    view.addEventListener("wheel", e => {
      e.preventDefault();
      const r = view.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
      const k = Math.exp(-e.deltaY * 0.0015), ns = Math.min(Math.max(s * k, 0.05), 20);
      x = mx - (mx - x) * ns / s; y = my - (my - y) * ns / s; s = ns;
      apply();
    }, {passive: false});
    let drag = null;
    view.addEventListener("pointerdown", e => { drag = {px: e.clientX, py: e.clientY, x, y}; view.setPointerCapture(e.pointerId); view.classList.add("drag"); });
    view.addEventListener("pointermove", e => { if (!drag) return; x = drag.x + e.clientX - drag.px; y = drag.y + e.clientY - drag.py; apply(); });
    const end = () => { drag = null; view.classList.remove("drag"); };
    view.addEventListener("pointerup", end); view.addEventListener("pointercancel", end);
  }

  window.KHCompare = {mount, prepare};
})();
