# kicad-hub desktop app

A window that sits beside KiCad and says, in plain English, what is left before the board
can be ordered. It watches the project folder; each time you save in KiCad it runs the
engine's quick check (`scripts/kicad_hub.py status`: ERC, DRC, the board against the
schematic, the BOM against `main`, missing fields and datasheets) and shows the result:
one sentence, five indicators (Schematic, Board, In sync, Parts, Datasheets) and the
things to tidy up, the ones that stop an order first. Full window, or a 400 px strip down
the right-hand side of the screen. Five themes.

Roadmap phase 7E. **Preview**: the quick check and the window work; *Show in KiCad*,
*Prepare the order* and the Windows installer (with its own Python) come next.

## Layout

```
app/
  ui/                  the page: index.html, app.js, app.css, fonts.css
    themes/            one file per theme, the 18 colours of each
    fonts/             Bricolage Grotesque, Figtree, DM Mono (SIL OFL 1.1), bundled
  src-tauri/           the core (Rust, Tauri 2): settings, watching, running the engine
  tests/ui.test.mjs    tests of the page in Chromium, with a stand-in core
  icon.svg             the app's icon (src-tauri/icons/ is made from it)
```

The page runs nothing itself. The core remembers the project, theme and window mode
(`settings.json` in the app's config folder), watches the project folder (only
`.kicad_pcb`, `.kicad_sch`, `.kicad_pro`, `.kicad_dru`; hidden folders and KiCad's
`*-backups` are ignored; it waits 1.5 s for a save to settle), runs the engine with no
console window and sends the page what the engine wrote to `.kicad-hub/status.json`. No web
server, no port. `.kicad-hub/` is kept out of git through `.git/info/exclude`, as the
plugin does; the engine's messages go to `.kicad-hub/app.log`.

## Run it from the repository

Needs Rust, a Python 3 on the PATH (or `KICAD_HUB_PYTHON`) and KiCad 10 (its `kicad-cli`
is found as the engine finds it, or give it with `KICAD_CLI`). On Linux also the
WebKitGTK development packages (`libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev`).

```
cargo run --manifest-path app/src-tauri/Cargo.toml
```

## Tests

```
NODE_PATH=$(npm root -g) node app/tests/ui.test.mjs [--shots DIR]   # the page (Playwright)
cargo test --manifest-path app/src-tauri/Cargo.toml                  # the core
```

`--shots DIR` saves a screenshot of every screen in both layouts and every theme. CI runs
both (`app` job in `.github/workflows/test.yml`).

## Windows

`cargo check --target x86_64-pc-windows-msvc` passes. The installer (NSIS, with the
embeddable Python, about 13 MB) is built with
`npx @tauri-apps/cli@2 build --runner cargo-xwin --target x86_64-pc-windows-msvc`
(needs `lld-link`, `llvm-rc`, `clang-cl` and `makensis`); see the roadmap, phase 7E step 5.
