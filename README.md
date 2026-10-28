# kicad-hub

Git-based hardware design review for KiCad, running entirely on GitHub.

The goal is to get the collaboration experience of commercial PCB cloud platforms
(web viewer, visual diffs, design reviews, release packages) for KiCad projects,
using only free and open-source tools on top of plain git, GitHub Actions and
GitHub Pages. No extra server to maintain.

> **Status:** early design phase. Nothing usable yet — see the [roadmap](docs/ROADMAP.md).

## What it will do

Every push or pull request on a KiCad project repository will:

- **Check** the design: run ERC and DRC and fail the PR if they don't pass.
- **Build** fabrication outputs: Gerbers, drill files, pick & place, BOM,
  schematic PDF and 3D renders.
- **Publish** a web portal on GitHub Pages for each branch / PR:
  - schematic and PCB viewer in the browser ([KiCanvas](https://kicanvas.org)),
  - interactive BOM ([InteractiveHtmlBom](https://github.com/openscopeproject/InteractiveHtmlBom)),
  - visual diff against the target branch,
  - downloadable fabrication package.
- **Comment** on the pull request with links to all of the above, plus a BOM diff
  (parts added, removed or changed) and missing-field checks (MPN, footprint,
  datasheet, voltage rating), and before/after images of the changed sheets and
  layers with the changes highlighted.
- **Price** the BOM: unit cost and stock per part, total per board and per batch,
  warnings for obsolete or out-of-stock parts.
- **Variants**: one BOM, cost and fab package per build variant of the same design.
- **Datasheets** linked from the BOMs and bundled in the package.
- **Release**: tagging `vX.Y` produces a versioned fab package in standard formats
  any fab house accepts (Gerber X2, Excellon, IPC-D-356 netlist, BOM and pick &
  place CSV, fab notes), with optional per-fab profiles on top.

Mockups: [PR comment](docs/mockups/pull-request.png) ·
[design portal](docs/mockups/portal.png).

## How the pieces fit together

```
 KiCad (each designer)
        │  git push / pull request
        ▼
 GitHub repository ──► GitHub Actions (Docker)
        │                 │  KiBot image (headless KiCad)
        │                 ├─ ERC / DRC          → PR goes red if they fail
        │                 ├─ Gerbers, drill, pick & place, BOM
        │                 ├─ schematic PDF, 3D render
        │                 ├─ interactive BOM (iBOM)
        │                 └─ visual diff against the target branch (Kiri / KiDiff)
        ▼
 GitHub Pages: one web portal per project
        ├─ KiCanvas viewer (schematic and PCB in the browser)
        ├─ iBOM
        ├─ diff of the revision
        └─ fabrication downloads
```

The same design should be portable to GitLab (GitLab CI + GitLab Pages + merge
requests) for teams that self-host.

## How it will be used

kicad-hub is a GitHub Action. A board project only needs a small workflow:

```yaml
# .github/workflows/kicad.yml in your board repository
name: KiCad
on:
  push:
    branches: [main]
  pull_request:
jobs:
  board:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          lfs: true
      - uses: JMcordobamendez/kicad-hub@main
        with:
          project-dir: hardware/my-board   # folder with the .kicad_pro
          # strict: true                   # fail on ERC/DRC errors (off while drafting)
```

To comment on pull requests and publish releases the workflow needs:

```yaml
on:
  push:
    branches: [main]
    tags: ["v*"]         # tagging vX.Y publishes the fab package
  pull_request:
permissions:
  contents: write        # releases (use read if you don't want them)
  pull-requests: write   # PR comments
```

Inputs:

| Input | Default | What it does |
|---|---|---|
| `project-dir` | — | Folder with the `.kicad_pro` |
| `strict` | `false` | Fail on ERC/DRC errors |
| `required-fields` | `Footprint,Datasheet,MPN\|LCSC` | Fields every fitted part needs; `A\|B` = either |
| `check-capacitor-voltage` | `true` | Capacitors need a `Voltage` field or a value like `10uF 16V` |
| `datasheet-dirs` | — | Extra folders with datasheet files (matched by MPN or value) |
| `datasheet-optional-refs` | `R,C,TP,H,MH,FID,JP,NT,LOGO` | Prefixes that don't need a datasheet |
| `check-datasheet-links` | `true` | Check that datasheet URLs respond |
| `extra-formats` | — | Extra formats in the fab package: `odb`, `ipc2581` |
| `release` | `true` | On tags, attach the fab package to a GitHub Release |
| `comment` | `true` | Post the report on the PR (one comment, updated on each push) |
| `output-dir` | `fab` | Where outputs are written |

Results:

- **Report** (job summary and PR comment): ERC/DRC counts, BOM changes against
  the PR's target branch (parts added, removed, changed), parts missing
  required fields and datasheet status (broken links, offline copies).
- **`package/<board>-<rev>-fab.zip`**: ready to send to any fab house. Gerber X2
  with job file (the board's own plot settings), Excellon with PTH/NPTH split and
  drill map, IPC-D-356 netlist, grouped BOM and pick & place in CSV, `README.txt`
  fab notes read from the board (size, layers, stackup, finish, smallest features,
  warnings when they are below the project rules) and `SHA256SUMS.txt`. The
  revision is the tag, else the title block revision, else the commit. On tags it
  is attached to a GitHub Release.
- **`datasheets/`** in the artifact: every local datasheet (referenced in the
  schematic or found by name in the datasheet folders) and an `index.html` linking
  each part to its datasheet.
- **Artifact `<board>-fab`**: ERC/DRC reports, schematic PDF, BOM (CSV/HTML) and,
  once the board has an outline on `Edge.Cuts`, Gerbers, drill, interactive BOM and
  pick & place. A board still without outline skips DRC and PCB outputs with a
  warning instead of failing.

ERC/DRC run with `kicad-cli`; outputs are produced by KiBot from
[`kibot/default.kibot.yaml`](kibot/default.kibot.yaml), inside the
`ghcr.io/inti-cmnb/kicad10_auto` image.

While kicad-hub is private, enable *Settings → Actions → General → Access →
"Accessible from repositories owned by the user"* so your other repos can use it.

New board repositories start from [`template/`](template/): folder layout,
`.gitignore`, Git LFS for 3D models, project libraries and a PR review checklist.

The platform evolves in this repository; every board project that uses it picks up
the improvements.

## Building blocks

| Need | Tool |
|---|---|
| Version control | git (built into KiCad 9/10) |
| Headless KiCad automation | [KiBot](https://github.com/INTI-CMNB/KiBot) (Docker image) |
| Web viewer | [KiCanvas](https://kicanvas.org) |
| Visual diff | [Kiri](https://github.com/leoheck/kiri) / KiDiff |
| Interactive BOM | InteractiveHtmlBom |
| CI and hosting | GitHub Actions + GitHub Pages |

## Known limits

- **No real-time co-editing.** KiCad files don't merge well: split work by
  hierarchical sheet and keep a single owner for the PCB at a time.
- **No component/supply-chain management** for now.
- Comments pinned to a location on the board (click a component → comment) are a
  possible later phase, not part of the first versions.

## License

To be decided before going public (probably MIT).
