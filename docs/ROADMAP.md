# Roadmap

Time estimates are rough, for one person.

## Where we are

```mermaid
flowchart LR
  classDef done fill:#1a7f37,stroke:#116329,color:#ffffff
  classDef doing fill:#d9622b,stroke:#a8461b,color:#ffffff
  classDef todo fill:#eaeef2,stroke:#8c959f,color:#1f2328
  classDef optional fill:#ffffff,stroke:#8c959f,color:#59636e,stroke-dasharray:4 3

  subgraph P1["1 · Template"]
    direction TB
    p1a["Layout, .gitignore, LFS<br/>KIPRJMOD libs, PR checklist"]:::done
    p1b["Tried on a real board<br/>(RiegoAutomatico PR)"]:::doing
  end

  subgraph P2["2 · CI with KiBot"]
    direction TB
    p2a["ERC / DRC + job summary"]:::done
    p2b["PDF, BOM, iBOM, Gerbers,<br/>drill, pick & place"]:::done
    p2c["Example board, first green run"]:::done
    p2d["Release zip on tag vX.Y"]:::done
  end

  subgraph P2B["2b · BOM, sourcing, manufacturing"]
    direction TB
    b1["1 · BOM diff in PR comment"]:::done
    b4["4 · Field checks<br/>MPN, footprint, datasheet, voltage"]:::done
    b6["6 · Datasheets"]:::done
    b5["5 · Variants"]:::doing
    b3["3 · Generic fab package<br/>(+ optional fab profiles)"]:::done
    b2["2 · Cost and stock"]:::todo
    b7["7 · Before/after images<br/>changes highlighted"]:::todo
  end

  subgraph P3["3 · Web portal"]
    direction TB
    p3["KiCanvas viewer, iBOM,<br/>downloads on GitHub Pages"]:::todo
  end

  subgraph P4["4 · Visual diff"]
    direction TB
    p4["Diff in the portal"]:::todo
  end

  subgraph P5["5 · Pinned comments"]
    direction TB
    p5["Click a part → PR comment"]:::optional
  end

  PUB(["Go public · v1"]):::todo

  P1 --> P2 --> P2B --> P3 --> P4 --> PUB
  P4 -.-> P5
  b7 -. same diff engine .- p4
```

**Legend:** green = done · orange = next / in progress · grey = planned ·
dashed = optional.

*Last update: 2026-10-28. Update the classes (`:::done`, `:::doing`, `:::todo`)
whenever a task changes state.*

## Phase 1 — Project template (~1 day)
Lives in `template/`.

- [x] Folder structure for a board repository
- [x] KiCad `.gitignore` (`*-backups/`, `fp-info-cache`, `*.lck`, `~*.lck`)
- [x] Git LFS for 3D models (`*.step`, `*.stp`, `*.wrl`) and LF line endings
      for KiCad files, in `.gitattributes`
- [x] Project libraries referenced with `${KIPRJMOD}` paths
- [x] Pull request template with a design review checklist (decoupling,
      clearances, fiducials…)
- [ ] Try it on a real personal board and adjust
- [ ] Make it usable as a GitHub template repository (or a `new-board` script)

## Phase 2 — CI with KiBot (~1–2 days)
First user: [RiegoAutomatico](https://github.com/JMcordobamendez/RiegoAutomatico)
(branch `feat/ci`), a board with schematic in progress and an empty PCB.

- [x] `kibot/default.kibot.yaml`: schematic PDF, BOM, iBOM, Gerbers, drill, pick & place
- [x] Composite action `action.yml` (instead of a reusable workflow: it can ship
      the KiBot config next to it and read it from `github.action_path`)
- [x] ERC/DRC with `kicad-cli`, counts in the job summary, reports in the artifact
- [x] Board without outline → DRC and PCB outputs skipped with a warning
- [x] `strict: true` makes ERC/DRC errors fail the job
- [x] Example board with a routed PCB: `examples/royalblue54l-feather` (KiCad demo,
      CERN-OHL-P), checked on every push by `.github/workflows/test.yml`
- [x] First green run on GitHub Actions (2026-10-28, example board: all outputs OK)
- [x] Standard KiCad libraries registered for kicad-cli in the container (ERC on the
      example went from 203 to 18 findings, DRC lost 67 spurious library warnings)

### 2b — BOM, sourcing and manufacturing

What a reviewer and a buyer need most, ranked by value. All of it is text or files
in the CI artifact and the PR comment, so it doesn't depend on the web portal.

1. **BOM diff in the PR comment** (cheap — do first)
   - [x] Compare the BOM of the PR branch with the target branch (per reference,
         `kicad-cli sch export bom`, base checked out in a git worktree)
   - [x] Post the changes as text: added / removed table + changed fields
         (`Value: 10k → 4k7`), one sticky comment per board updated on each push
   - [x] Confirmed on a real PR on GitHub (kicad-hub #1, 2026-10-28)
2. **Cost and availability**
   - [ ] Unit price and stock per part from LCSC / Mouser / DigiKey (KiBot + KiCost)
   - [ ] Total per board and per batch (quantity as an input, e.g. 7 boards)
   - [ ] Warn on obsolete, not-recommended or out-of-stock parts
   - [ ] API keys as repository secrets; skip gracefully when they are missing
3. **Ready-to-order package — generic, any fab house**
   - [x] One zip in standard formats every fab accepts, no vendor lock-in, built on
         every run and attached to a GitHub Release on tag `vX.Y`:
     - Gerber X2 (+ `.gbrjob`) and Excellon drill (PTH/NPTH split, drill map)
     - IPC-D-356 netlist, so the fab can run its electrical test against the design
     - Assembly: BOM (CSV with MPN, manufacturer, quantity, references, DNP) and
       pick & place (CSV, mm, top/bottom)
     - Fab notes (`README.txt`): board name, revision, layer count, thickness,
       stackup, finish, min track/space and drill, generated from the KiCad project
     - Optional ODB++ / IPC-2581 (KiCad exports both) for fabs that prefer them
   - [x] File names with board name + revision; checksum list inside the zip
   - [x] Fab notes warn when the smallest features used are below the project rules
         (example board: vias with a 0.00001 mm drill)
   - [x] Confirmed on GitHub: tag `v0.0.1-test` → release with
         `RoyalBlue54L-Feather-v0.0.1-test-fab.zip` (666 KB)
   - [ ] Fab profiles as an **optional** layer on top (`fab-profile: jlcpcb |
         pcbway | eurocircuits …`): only renames columns/files and applies that
         fab's DRC rules. Default: `generic`
4. **Checks the ERC doesn't do** (cheap — do with 1)
   - [x] Every part has MPN, footprint and datasheet (except `DNP` / not-in-BOM);
         configurable with `required-fields`, `A|B` alternatives (`MPN|LCSC`)
   - [x] Capacitors have a voltage rating field (or a value like `10uF 16V`)
   - [ ] DRC against the project's own rules by default; a fab profile can add that
         fab's rules (`.kicad_dru`)
   - [x] Report missing fields as a list in the job summary and PR comment
         (example board: 42 of 59 parts, mostly datasheet and capacitor voltage)
5. **Variants** — same design, different builds (e.g. irrigation nodes with or
   without level sensor)
   - [ ] Variant definition in the board repo (KiBot variants, KiBoM style)
   - [ ] One BOM, cost and fab package per variant; DNP parts per variant listed
   - [ ] Variant as a dimension in the BOM diff
6. **Datasheets**
   - [x] `datasheets/index.html` in the artifact linking every part to its datasheet
         (URL and/or offline copy)
   - [ ] Datasheet column as a clickable link in the KiBot HTML BOM and the iBOM
   - [x] Local datasheets resolved and copied into the artifact, so the package is
         self-contained: paths in the Datasheet field (`${KIPRJMOD}` or relative), and
         files in datasheet folders matched by MPN/value, family names included
         (`S-82A2A_B_C.pdf` → `S-82A2AAA-A8T2U`). Offline copy even when a URL exists
   - [x] Check that every datasheet link or file exists: 404/410/DNS = broken,
         sites that block bots (403, timeouts) = unverified, not counted as broken
   - [x] Generic parts (R, C, test points, holes, fiducials…) don't need a datasheet
         (`datasheet-optional-refs`)
   - [ ] (Phase 3) open the datasheet from the selected part in the web viewer
7. **Before/after images in the PR comment, with the changes highlighted**
   - [ ] Render the changed schematic sheets and PCB layers for both branches (SVG/PNG)
   - [ ] Highlight what changed: added in green, removed in red, unchanged dimmed
         (pixel/vector diff, as Kiri/KiDiff do), plus a box around each changed area
   - [ ] Only the sheets/layers that changed, to keep the comment short
   - [ ] Images embedded in the PR comment (not just a link), each linking to the
         full-size diff
   - [ ] Shares the diff engine with phase 4 (visual diff in the portal)
- [x] Tag `vX.Y` → fabrication zip named with board name and revision, attached to
      a GitHub Release (see 2b.3; confirmed with tag v0.0.1-test, 2026-10-28)

## Phase 3 — Web portal (~2–3 days)
- [ ] Static page per commit or branch on GitHub Pages
- [ ] KiCanvas viewer embedded
- [ ] Interactive BOM and downloads
- [ ] Bot comment on the PR with links: "view design", "view diff", "BOM"

## Phase 4 — Visual diff (~1–2 days)
- [ ] Kiri or KiDiff comparing the PR branch with the target branch
- [ ] Diff HTML published in the portal and linked from the PR comment

## Phase 5 (optional) — Comments on the design
The only piece no existing tool provides. A small web app on top of KiCanvas:
clicking a component creates a PR review comment through the GitHub API
(e.g. "U12 (x, y): missing decoupling").

- [ ] Prototype

This is a software project in its own right. Leave it until phases 1–4 work and
it's clear the feature is actually missed.

## Later
- [ ] GitLab port (GitLab CI + Pages + merge requests) for self-hosted teams
- [ ] Microcontroller pin map from the schematic (GPIO → net) as a table and a
      `pinout.h`, so firmware and board don't drift apart
- [ ] Notifications (e.g. Telegram) when CI finishes or a release is published
- [ ] 3D render output (slow, low value per push: maybe only on releases)

## Before going public
- [ ] Choose license
- [ ] Documentation and a working example board
- [ ] Avoid third-party trademarks in names and descriptions

## Decisions so far
- **Build on GitHub, not on a custom server.** Actions + Pages are free for public
  repos; nothing to host or maintain.
- **Two kinds of repositories.** This repo is the platform (reusable action +
  template). Each board lives in its own repo and calls the action.
- **Reuse existing tools** (KiBot, KiCanvas, Kiri, iBOM) instead of writing our own.
- **Developed as a personal project**, with personal designs only.
- **Usefulness over copying commercial platforms** (2026-10-28): BOM diff, cost,
  ready-to-order packages, extra checks, variants and datasheets (phase 2b) come
  before the web viewer polish. Pinned comments on the board (phase 5) stay
  optional: too much work for a one-person team.
- **Fab-house agnostic** (2026-10-28): the release package uses standard formats
  (Gerber X2, Excellon, IPC-D-356, CSV BOM/CPL, fab notes) that any fab accepts.
  Vendor formats (JLCPCB, PCBWay…) are optional profiles, never the default.
- **Web portal needs public repos** (or GitHub Pro): GitHub Pages is free only
  for public repositories.
- **Mockups** of the PR comment and the portal: `docs/mockups/`.
