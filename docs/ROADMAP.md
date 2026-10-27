# Roadmap

## Phase 1 — Project template
- [ ] Folder structure for a board repository
- [ ] KiCad `.gitignore` (`*-backups/`, `fp-info-cache`, `*.lck`, `~*.lck`)
- [ ] Git LFS for 3D models (`*.step`, `*.stp`, `*.wrl`)
- [ ] Project libraries referenced with `${KIPRJMOD}` paths
- [ ] Pull request template with a design review checklist

## Phase 2 — CI with KiBot
- [ ] `.kibot.yaml` with all outputs
- [ ] Reusable workflow `.github/workflows/kicad.yml`
- [ ] ERC/DRC block the merge when they fail
- [ ] Tag `vX.Y` → versioned fabrication zip attached to a GitHub Release

## Phase 3 — Web portal
- [ ] Static page per branch/PR on GitHub Pages
- [ ] KiCanvas viewer embedded
- [ ] Interactive BOM and downloads
- [ ] Bot comment on the PR with links

## Phase 4 — Visual diff
- [ ] Kiri or KiDiff comparing the PR branch with the target branch
- [ ] Diff published in the portal and linked from the PR comment

## Phase 5 (optional) — Comments on the design
- [ ] Small web app on top of KiCanvas: click a component → create a PR review
      comment through the GitHub API (e.g. "U12 (x, y): missing decoupling")

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
