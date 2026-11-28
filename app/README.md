# kicad-hub desktop app

A window that sits beside KiCad and says, in plain English, what is left before the board
can be ordered. It watches the project folder; each time you save in KiCad it runs the
engine's quick check (`scripts/kicad_hub.py status`: ERC, DRC, the board against the
schematic, the BOM against `main`, missing fields and datasheets) and shows the result:
one sentence, five indicators (Schematic, Board, In sync, Parts, Datasheets) and the
things to tidy up, the ones that stop an order first. Full window, or a 400 px strip down
the right-hand side of the screen. Five themes.

Roadmap phase 7E. **Preview**: the quick check, the window, *Show in KiCad* and *Prepare
the order* work; the Windows installer (with its own Python) comes next.

**Prepare the order** runs the slow steps (`kicad_hub.py run --skip portal`: datasheet
links, prices and stock, the fab package, requirements, report; about three minutes on a
large board) and shows the cost per board for each batch, the parts short on stock, the
fab package and what to check before ordering (`.kicad-hub/order.json`).

**Show in KiCad** selects a row's items on the board open in KiCad and zooms to them
(`python/show_in_kicad.py`, with kicad-python, which the installer bundles). KiCad must
have the board open in the PCB editor and *Preferences → Plugins → Enable KiCad API*
ticked.

## Layout

```
app/
  ui/                  the page: index.html, app.js, app.css, fonts.css
    themes/            one file per theme, the 18 colours of each
    fonts/             Bricolage Grotesque, Figtree, DM Mono (SIL OFL 1.1), bundled
  src-tauri/           the core (Rust, Tauri 2): settings, watching, running the engine
  python/              show_in_kicad.py: selects a row's items in KiCad (kicad-python)
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

Needs Rust, a Python 3 on the PATH (or `KICAD_HUB_PYTHON`; with `pip install
kicad-python` for Show in KiCad) and KiCad 10 (its `kicad-cli`
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

## The Windows installer

Built on a Windows runner by `.github/workflows/app.yml` (on pull requests that touch the
app, and by the Release workflow, which attaches it to the release):

```
python app/scripts/bundle_python.py      # app/src-tauri/python: embeddable Python + kicad-python
cd app && npx @tauri-apps/cli@2.12.6 build --bundles nsis
```

`tauri.windows.conf.json` adds the Python to the bundle, next to the engine (`scripts/`,
`fab-profiles/`) and `python/show_in_kicad.py`. The installer installs for the current
user, without admin rights. It is not signed yet: Windows SmartScreen asks once
("More info → Run anyway").

Only one window runs: starting the app again brings it to the front, and
`kicad-hub --project <folder>` also switches it to that project. That is what the KiCad
plugin's button does when the app is installed.
