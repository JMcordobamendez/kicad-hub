# Roadmap

Time estimates are rough, for one person.

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
- [ ] First green run on GitHub Actions (not tested yet: no Docker on the dev PC)

### 2b — BOM, sourcing and manufacturing

What a reviewer and a buyer need most, ranked by value. All of it is text or files
in the CI artifact and the PR comment, so it doesn't depend on the web portal.

1. **BOM diff in the PR comment** (cheap — do first)
   - [ ] Compare the BOM of the PR branch with the target branch
   - [ ] Post the changes as text: `+ C14 100nF 0603`, `R7: 10k → 4k7`, `− U5`
   - [ ] Group by added / removed / changed value / changed footprint
2. **Cost and availability**
   - [ ] Unit price and stock per part from LCSC / Mouser / DigiKey (KiBot + KiCost)
   - [ ] Total per board and per batch (quantity as an input, e.g. 7 boards)
   - [ ] Warn on obsolete, not-recommended or out-of-stock parts
   - [ ] API keys as repository secrets; skip gracefully when they are missing
3. **Ready-to-order package**
   - [ ] On tag `vX.Y`: zip in the exact format of a fab house (JLCPCB first:
         Gerbers + BOM + CPL with their column names; KiBot ships templates)
   - [ ] Fab house as an input, so others can be added later
4. **Checks the ERC doesn't do** (cheap — do with 1)
   - [ ] Every part has MPN, footprint and datasheet (except `DNP` / not-in-BOM)
   - [ ] Capacitors have a voltage rating field
   - [ ] DRC against the chosen fab house rules (`.kicad_dru`) instead of defaults
   - [ ] Report missing fields as a list in the job summary and PR comment
5. **Variants** — same design, different builds (e.g. irrigation nodes with or
   without level sensor)
   - [ ] Variant definition in the board repo (KiBot variants, KiBoM style)
   - [ ] One BOM, cost and fab package per variant; DNP parts per variant listed
   - [ ] Variant as a dimension in the BOM diff
6. **Datasheets**
   - [ ] Datasheet column as a link in the HTML BOM and the interactive BOM
   - [ ] Local datasheets in the board repo (e.g. `hardware/datasheet/`) resolved
         and copied into the artifact, so the package is self-contained
   - [ ] Check that every datasheet link or file exists
   - [ ] (Phase 3) open the datasheet from the selected part in the web viewer
7. **Before/after images in the PR comment, with the changes highlighted**
   - [ ] Render the changed schematic sheets and PCB layers for both branches (SVG/PNG)
   - [ ] Highlight what changed: added in green, removed in red, unchanged dimmed
         (pixel/vector diff, as Kiri/KiDiff do), plus a box around each changed area
   - [ ] Only the sheets/layers that changed, to keep the comment short
   - [ ] Images embedded in the PR comment (not just a link), each linking to the
         full-size diff
   - [ ] Shares the diff engine with phase 4 (visual diff in the portal)
- [ ] Tag `vX.Y` → fabrication zip named with board name and revision, attached to
      a GitHub Release

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
- **Web portal needs public repos** (or GitHub Pro): GitHub Pages is free only
  for public repositories.
- **Mockups** of the PR comment and the portal: `docs/mockups/`.
