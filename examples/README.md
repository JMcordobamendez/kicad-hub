# Examples

Boards used to test the action on every push (`.github/workflows/test.yml`).

## royalblue54l-feather

RoyalBlue54L Feather: nRF54L15 + nPM1300 PMIC + onboard debugger, hierarchical
schematic, project libraries through `${KIPRJMOD}`.

- Author: Lord's Boards — <https://www.crowdsupply.com/lords-boards/royalblue54l-feather>
- License: CERN-OHL-P v2 (see `royalblue54l-feather/LICENSE`)
- Copied unmodified from the demos shipped with KiCad 10.0
  (`share/kicad/demos/royalblue54L_feather`), without the `img/` folder (README
  pictures) and the separate `nfc_antenna/` project.

Opened with a stock KiCad install it reports many ERC/DRC violations (library
mismatches, drill sizes outside the default rules). That's expected and useful:
the action runs in non-strict mode and shows them in the job summary.
