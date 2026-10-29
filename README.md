# kicad-hub

Git-based hardware design review for KiCad, running entirely on GitHub.

The goal is the collaboration experience of commercial PCB cloud platforms
(design checks, BOM review, release packages, web viewer, visual diffs) for KiCad
projects, using only free and open-source tools on top of plain git, GitHub Actions
and GitHub Pages. No extra server to maintain.

> **Status:** the CI part works and is tested on every push with a real board
> (see [what works today](#what-works-today)). The web portal and visual diffs are
> next — see the [roadmap](docs/ROADMAP.md).

## What works today

On every push and pull request of a KiCad project, the action:

- **Checks the design**: ERC and DRC (plus schematic/PCB parity) with `kicad-cli`.
  Non-blocking by default; `strict: true` fails the job on errors.
- **Checks the BOM**: every fitted part needs the required fields (by default
  footprint, datasheet and MPN or LCSC code); capacitors need a voltage rating.
- **Diffs the BOM** of a pull request against its target branch: parts added,
  removed and changed (`Value: 10uF → 22uF`).
- **Resolves the datasheets**: links checked (broken vs. unverifiable), local PDFs
  found by MPN or value in the datasheet folders and bundled for offline use.
- **Prices the BOM from several suppliers**: price and stock of every part at LCSC
  and JLCPCB (no API key needed) and at Mouser, Farnell and DigiKey (with your own
  free API keys); cost per board and per batch (1, 5, 10, 100 boards) for every
  variant; a page to compare offers part by part and choose the supplier, saved in
  the repo. Warns on parts out of stock, not active, not found, or whose LCSC code
  doesn't match the MPN.
- **Builds a fab package** in standard formats any fab house accepts (Gerber X2,
  Excellon, IPC-D-356, BOM and pick & place CSV) with fab notes read from the
  board, which warn about features below the project rules.
- **Reports** all of the above in the job summary and as **one comment on the PR**,
  updated on every push.
- **Builds every design variant** (KiCad 10 variants, read from the project): the
  report lists what each one fits, leaves out or changes, and the fab package gets
  a BOM and pick & place per variant.
- **Shows before/after images** of the schematic sheets and PCB layers changed in a
  pull request, right in the PR comment: removed in red, added in green, each
  changed area boxed.
- **Publishes a GitHub Release** with the fab package when you tag `vX.Y`.
- Also: schematic PDF, BOM (CSV/HTML) and interactive BOM.

A board still being drawn (no outline on `Edge.Cuts`) skips DRC and the PCB outputs
with a warning instead of failing.

## Planned

- **Web portal** on GitHub Pages: schematic/PCB viewer in the browser
  ([KiCanvas](https://kicanvas.org)), iBOM, downloads.
- **Visual diff** in the portal.

Mockups: [PR comment](docs/mockups/pull-request.png) ·
[design portal](docs/mockups/portal.png). Details and progress:
[roadmap](docs/ROADMAP.md).

## How the pieces fit together

```
 KiCad (each designer)
        │  git push / pull request / tag
        ▼
 GitHub repository ──► GitHub Actions ── kicad-hub action (Docker: KiCad 10 + KiBot)
                          │
                          ├─ kicad-cli ── ERC / DRC
                          │            ── per-part BOM (this commit + PR base)
                          │            ── SVG of every sheet/layer (this commit + PR base)
                          │            ── fab package: Gerber X2, Excellon,
                          │               IPC-D-356, BOM, pick & place
                          ├─ Python ──── scripts/datasheets.py  resolve, check, bundle
                          │            ── scripts/fabnotes.py    fab notes from the board
                          │            ── scripts/sourcing.py    cost and stock (LCSC, JLCPCB,
                          │                                      Mouser, Farnell, DigiKey)
                          │            ── scripts/visual_diff.py before/after images
                          │            ── scripts/report.py      markdown report
                          ├─ KiBot ───── schematic PDF, BOM CSV/HTML, iBOM
                          │
                          ├──► job summary + sticky PR comment (images: branch kicad-hub-images)
                          ├──► artifact <board>-fab
                          └──► GitHub Release (on tags)

 (planned) GitHub Pages portal: KiCanvas viewer, iBOM, visual diff, downloads
```

`kicad-cli` is used wherever possible: it respects the settings saved in the
project (plot layers, rules) and its options are stable. KiBot is kept for the
outputs it does best.

## Usage

kicad-hub is a GitHub Action. A board repository only needs a small workflow:

```yaml
# .github/workflows/kicad.yml in your board repository
name: KiCad

on:
  push:
    branches: [main]
    tags: ["v*"]         # tagging vX.Y publishes the fab package as a release
  pull_request:

permissions:
  contents: write        # releases and PR images (read = neither)
  pull-requests: write   # PR comment with the report

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
```

While kicad-hub is private, enable *Settings → Actions → General → Access →
"Accessible from repositories owned by the user"* in kicad-hub so your other repos
can use it.

### Inputs

| Input | Default | What it does |
|---|---|---|
| `project-dir` | — | Folder with the `.kicad_pro` (required) |
| `strict` | `false` | Fail on ERC/DRC errors |
| `required-fields` | `Footprint,Datasheet,MPN\|LCSC` | Fields every fitted part needs; `A\|B` = either |
| `check-capacitor-voltage` | `true` | Capacitors need a `Voltage` field or a value like `10uF 16V` |
| `datasheet-dirs` | — | Extra folders with datasheet files (matched by MPN or value) |
| `datasheet-optional-refs` | `R,C,TP,H,MH,FID,JP,NT,LOGO` | Prefixes that don't need a datasheet |
| `check-datasheet-links` | `true` | Check that datasheet URLs respond |
| `variants` | `auto` | Design variants to build: `auto` (all in the project), a list, or `none` |
| `sourcing` | `true` | Price and stock per supplier, and the comparison page |
| `boards` | `1,5,10,100` | Batch sizes to price (components only) |
| `currency` | `USD` | Currency for all prices (converted with ECB rates) |
| `sourcing-choices` | `kicad-hub-sourcing.csv` | Supplier picked per part, relative to `project-dir` (see [choosing suppliers](#choosing-suppliers)) |
| `mouser-api-key` | — | Mouser Search API key, from a secret ([how](#supplier-api-keys)) |
| `farnell-api-key` | — | Farnell / element14 API key, from a secret |
| `farnell-store` | `uk.farnell.com` | Farnell store to price from (`es.farnell.com`, `de.farnell.com`, `www.newark.com`…) |
| `digikey-client-id` | — | DigiKey API client ID, from a secret |
| `digikey-client-secret` | — | DigiKey API client secret, from a secret |
| `visual-diff` | `true` | On PRs, before/after images of the changed sheets and layers in the comment (stored in the `kicad-hub-images` branch) |
| `extra-formats` | — | Extra formats in the fab package: `odb`, `ipc2581` |
| `release` | `true` | On tags, attach the fab package to a GitHub Release |
| `comment` | `true` | Post the report on the PR |
| `github-token` | `github.token` | Token for the PR comment and the release |
| `image` | `ghcr.io/inti-cmnb/kicad10_auto:1.9.1` | Docker image with KiCad and KiBot |
| `output-dir` | `fab` | Where outputs are written |

Outputs: `has-pcb`, `missing-fields`.

Datasheet folders searched by default: `datasheet(s)/` and `docs/` next to the
project, and `datasheet(s)/` one level up. A file is used when its name contains
the part's MPN or value, or its family code (`S-82A2A_B_C.pdf` →
`S-82A2AAA-A8T2U`).

### Supplier API keys

LCSC and JLCPCB work out of the box: parts are looked up by their `LCSC` field
(e.g. `C25744`), no account needed. Mouser, Farnell and DigiKey need an API key,
which each user gets for free with their own account — kicad-hub has no shared
keys. Parts are looked up there by their `MPN` field, or by the manufacturer part
number LCSC reports when the MPN is empty. Add only the suppliers you want; the
others are skipped and listed as "no API key" in the report.

**1. Get the keys**

- **Mouser** — sign in at [mouser.com](https://www.mouser.com) (create an account if
  needed), open the [API Hub](https://www.mouser.com/api-hub/) and sign up for the
  **Search API**. The key is shown under *My Account → APIs* and sent by email.
  Limits: 30 requests per minute, 1,000 per day (one request per part).
- **Farnell / element14** — register at
  [partner.element14.com](https://partner.element14.com/), then *Register an
  application* / *Get API Keys* for the **Product Search API (REST)**. Copy the key.
  Pick your store with `farnell-store` (prices come in that store's currency).
- **DigiKey** — sign in at [developer.digikey.com](https://developer.digikey.com/)
  with a DigiKey account, create an organization, then a **Production App** with
  the **ProductInformation V4** API enabled (the OAuth callback can be
  `https://localhost`, it isn't used). Copy its **Client ID** and **Client Secret**.

**2. Store them as secrets of your board repository** (never in a file): *Settings →
Secrets and variables → Actions → New repository secret*, one per value:

| Secret name | Value |
|---|---|
| `MOUSER_API_KEY` | Mouser Search API key |
| `FARNELL_API_KEY` | element14 API key |
| `DIGIKEY_CLIENT_ID` | DigiKey Client ID |
| `DIGIKEY_CLIENT_SECRET` | DigiKey Client Secret |

**3. Pass them to the action** in your workflow:

```yaml
      - uses: JMcordobamendez/kicad-hub@main
        with:
          project-dir: hardware/my-board
          currency: EUR
          mouser-api-key: ${{ secrets.MOUSER_API_KEY }}
          farnell-api-key: ${{ secrets.FARNELL_API_KEY }}
          farnell-store: es.farnell.com
          digikey-client-id: ${{ secrets.DIGIKEY_CLIENT_ID }}
          digikey-client-secret: ${{ secrets.DIGIKEY_CLIENT_SECRET }}
```

A secret that isn't set arrives empty and that supplier is skipped, so the same
workflow works with any subset. GitHub masks secrets in the logs. Pull requests
from forks don't get secrets: they are priced with LCSC and JLCPCB only.

### Choosing suppliers

By default each part is bought from the cheapest supplier that has enough stock for
the batch. To choose yourself:

1. Download the artifact of a run and open **`sourcing.html`**: one row per part,
   with every supplier's stock, minimum order, unit price and line total for the
   batch you select, and totals per build and batch.
2. Pick an offer per part (or *Use the cheapest for every part* to freeze the
   current choice). **Download purchase list** gives one CSV per batch, sorted by
   supplier, ready to paste into each supplier's cart.
3. **Download selection file** and commit it as `kicad-hub-sourcing.csv` next to
   the `.kicad_pro`. From then on the report's totals use your picks; parts not in
   the file stay automatic. It's a plain CSV (`Part,Supplier,SKU`, where `Part` is
   the MPN or the LCSC code), so it can also be edited by hand and reviewed in PRs.

### What you get

**Report** — job summary and PR comment:
ERC/DRC counts · BOM changes vs. the PR base · before/after images · variants · parts missing required fields ·
datasheet status (broken links, offline copies) · cost per batch and stock warnings ·
fab package warnings.

**Artifact `<board>-fab`:**

```
reports/      erc/drc (.rpt, .json), per-part BOMs, report.md, datasheets.json,
              sourcing.csv (every supplier offer per part and batch)
sourcing.html supplier comparison: choose per part, download selection and purchase list
package/      <board>-<rev>-fab.zip  ← send this to the fab
  <board>-<rev>/
    gerber/       Gerber X2 + .gbrjob (layers as set in the board's plot settings)
    drill/        Excellon PTH and NPTH, drill maps (PDF)
    assembly/     BOM (CSV, grouped, no DNP) and pick & place (CSV, mm, both sides)
      <variant>/  the same for each design variant
    *.d356        IPC-D-356 netlist for electrical test
    README.txt    fab notes: size, layers, stackup, finish, smallest features, warnings
    SHA256SUMS.txt
visual/       PRs only: before/after (<page>.png) and full-page overlay (<page>-full.png)
datasheets/   local datasheets + index.html (part → datasheet)
schematic/    schematic PDF
bom/          BOM CSV and HTML (KiBot)
ibom/         interactive BOM
```

The package revision is the tag on releases, else the title block revision, else
the commit hash.

## Repository layout

```
action.yml                  the action
scripts/                    report.py, datasheets.py, fabnotes.py, variant_cpl.py,
                            visual_diff.py, sourcing.py (+ sourcing.html, the comparison page)
kibot/default.kibot.yaml    KiBot outputs (schematic PDF, BOMs, iBOM)
template/                   starting point for a new board repository
examples/                   boards used to test the action (see examples/README.md)
.github/workflows/test.yml  runs the action on the example board on every push/PR/tag
docs/                       roadmap and mockups
```

New board repositories start from [`template/`](template/): folder layout,
`.gitignore`, `.gitattributes` (LF for KiCad files, Git LFS for 3D models),
project libraries through `${KIPRJMOD}` and a PR design review checklist.

## Development

- Every push to `main`, every PR and every `v*` tag runs the action on
  `examples/royalblue54l-feather` (`.github/workflows/test.yml`). A PR shows the
  report comment, a tag publishes a release.
- The Python scripts can be run locally against files exported with a local
  `kicad-cli` (see the arguments at the top of each script). `fabnotes.py` needs
  KiCad's own Python (`pcbnew`); on Windows it may open a "font not found" dialog
  that blocks until closed — that doesn't happen in CI.

## Known limits

- **No real-time co-editing.** KiCad files don't merge well: split work by
  hierarchical sheet and keep a single owner for the PCB at a time.
- **No web viewer yet**, and GitHub Pages is free only for public repositories.
- Comments pinned to a location on the board are an optional later phase.

## License

To be decided before going public (probably MIT). The example board keeps its own
license (CERN-OHL-P, see [examples/README.md](examples/README.md)).
