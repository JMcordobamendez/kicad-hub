# Vendored libraries

Used by the design portal (`scripts/portal.html`); `portal.py index` copies them to `assets/` of the site branch.

| File | What | Source | License | sha256 |
|---|---|---|---|---|
| kicanvas.js | KiCanvas, the KiCad viewer (no version string, "KiCanvas Alpha") | https://kicanvas.org/kicanvas/kicanvas.js, downloaded 2026-10-29 (https://github.com/theacodes/kicanvas) | MIT | ca910f25276c3efb9aacb3a5d6341d4d9af4736d4c875fb0440d2cc856865ab7 |
| marked.min.js | marked v12.0.2, Markdown to HTML | jsdelivr (npm `marked@12.0.2`) | MIT | 15fabce5b65898b32b03f5ed25e9f891a729ad4c0d6d877110a7744aa847a894 |
| purify.min.js | DOMPurify 3.1.6, HTML sanitizer | jsdelivr (npm `dompurify@3.1.6`) | Apache-2.0 / MPL-2.0 | c0845096a7c4a6741f362ac506c94c1c7d27dc603bcc1bf64a587f76f2dbe3a1 |

Pinned on purpose: the portal relies on KiCanvas internals (the shadow DOM viewer, `project.pages()`,
`set_active_page`, `flip_view`, layers and the `kicanvas:select` event). When updating any of them,
re-test those.
