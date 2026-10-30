# Vendored libraries

Used by the design portal (`scripts/portal.html`); `portal.py index` copies them to `assets/` of the site branch.

| File | What | Source | License | sha256 |
|---|---|---|---|---|
| kicanvas.js | KiCanvas with schematic net picking and highlight: the fork https://github.com/JMcordobamendez/kicanvas, branch `feat/schematic-net-select`, commit 3f3ffa1ae7bb222a1c27e22f48246b69f6e9cafd (e4c945f on the branch only formats it) on top of upstream b031159 (the build we had before, from https://kicanvas.org downloaded 2026-10-29). Built by the fork's "Fork CI" run 41058199795 (2026-10-30), `npm run build` | fork of https://github.com/theacodes/kicanvas | MIT | 931b32d63c7a0b3c5f36f4ac2441c164ef99806e6b015341d41a8f3a0b1acd01 |
| marked.min.js | marked v12.0.2, Markdown to HTML | jsdelivr (npm `marked@12.0.2`) | MIT | 15fabce5b65898b32b03f5ed25e9f891a729ad4c0d6d877110a7744aa847a894 |
| purify.min.js | DOMPurify 3.1.6, HTML sanitizer | jsdelivr (npm `dompurify@3.1.6`) | Apache-2.0 / MPL-2.0 | c0845096a7c4a6741f362ac506c94c1c7d27dc603bcc1bf64a587f76f2dbe3a1 |

Pinned on purpose: the portal relies on KiCanvas internals (the shadow DOM viewer, `project.pages()`,
`set_active_page`, `flip_view`, layers and the `kicanvas:select` event). When updating any of them,
re-test those.

The fork adds to the schematic viewer (everything else is upstream's): wires, buses, junctions, labels,
power symbols and pins can be picked (the `kicanvas:select` item is the wire, label, pin... and
`viewer.net_of(item)` gives its net), `viewer.netlist` (connectivity across the sheets, built from the
project), `viewer.highlight_net(net | name | null)` (dims the rest, draws the net, survives sheet changes),
the `kicanvas:nethighlight` event, `viewer.sheet_path` (set when a project page is opened) and
`viewer.apply_netlist(text)` (names from `kicad-cli sch export netlist`, not used yet). `scripts/crossprobe.js`
uses them and falls back to its rings when they are missing. To update: rebase the fork on upstream, let its
"Fork CI" pass (lint, 109 unit tests, viewer tests), download the `kicanvas-build` artifact, replace the file,
update this table, and run the "Docs screenshots" workflow with the `xprobe` page.
