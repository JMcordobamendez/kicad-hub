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
- [ ] `.kibot.yaml` with all outputs
- [ ] Reusable workflow `.github/workflows/kicad.yml` that runs it on every push
- [ ] ERC/DRC block the merge when they fail
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
