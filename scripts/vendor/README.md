# Vendored libraries

Used by the design portal (`scripts/portal.html`); `portal.py index` copies them to `assets/` of the site branch.

| File | What | Source | License | sha256 |
|---|---|---|---|---|
| kicanvas.js | KiCanvas with schematic net picking and highlight: the fork https://github.com/JMcordobamendez/kicanvas, branch `feat/schematic-net-select`, commit 8987662c76d3896e9ee84485619128907b6f52aa on top of upstream b031159 (the build we had before, from https://kicanvas.org downloaded 2026-10-29). Built by the fork's "Fork CI" run 41069545825 (2026-10-30), `npm run build` | fork of https://github.com/theacodes/kicanvas | MIT | 95efae4571f3a5f3e11ee30235aa524c4eba8572e5f2bacfbcaec0f4352a96cd |
| marked.min.js | marked v12.0.2, Markdown to HTML | jsdelivr (npm `marked@12.0.2`) | MIT | 15fabce5b65898b32b03f5ed25e9f891a729ad4c0d6d877110a7744aa847a894 |
| purify.min.js | DOMPurify 3.1.6, HTML sanitizer | jsdelivr (npm `dompurify@3.1.6`) | Apache-2.0 / MPL-2.0 | c0845096a7c4a6741f362ac506c94c1c7d27dc603bcc1bf64a587f76f2dbe3a1 |

Pinned on purpose: the portal relies on KiCanvas internals (the shadow DOM viewer, `project.pages()`,
`set_active_page`, `flip_view`, layers and the `kicanvas:select` event). When updating any of them,
re-test those.

The fork adds to the schematic viewer (everything else is upstream's): wires, buses, junctions, labels,
power symbols and pins can be picked (the `kicanvas:select` item is the wire, label, pin... and
`viewer.net_of(item)` gives its net), `viewer.netlist` (connectivity across the sheets, built from the
project), `viewer.highlight_net(net | name | null)` (dims the rest, draws the net in vivid yellow with a halo, survives sheet changes),
the `kicanvas:nethighlight` event, `viewer.sheet_path` (set when a project page is opened) and
`viewer.apply_netlist(text)` (names from `kicad-cli sch export netlist`, not used yet). `scripts/crossprobe.js`
uses them and falls back to its rings when they are missing. To update: rebase the fork on upstream, let its
"Fork CI" pass (lint, 112 unit tests, 9 viewer tests), download the `kicanvas-build` artifact, replace the file,
update this table, and run the "Docs screenshots" workflow with the `xprobe` page.
