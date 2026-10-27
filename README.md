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
- **Comment** on the pull request with links to all of the above.
- **Release**: tagging `vX.Y` produces a versioned, ready-to-order fab package.

## How it will be used

kicad-hub is a reusable GitHub Action. A board project only needs a small workflow:

```yaml
# .github/workflows/kicad.yml in your board repository
on: [push, pull_request]
jobs:
  kicad:
    uses: JMcordobamendez/kicad-hub/.github/workflows/kicad.yml@v1
```

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
