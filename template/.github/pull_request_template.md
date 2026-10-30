## What changes

<!-- Short description. Which sheets / board areas are affected? -->

## Design review checklist

### Schematic
- [ ] ERC passes (or every remaining warning is justified below)
- [ ] Decoupling capacitors next to every IC supply pin
- [ ] Pull-ups / pull-downs on open-drain lines, resets, boot and enable pins
- [ ] Voltage ratings: capacitors, ESD/TVS, connectors
- [ ] Every part has value, footprint, manufacturer and MPN fields
- [ ] Net names are meaningful on buses and inter-sheet signals

### PCB
- [ ] DRC passes against the fab house rules
- [ ] Clearances and track widths fit the current and voltage of each net
- [ ] Decoupling placed and routed short to the pins
- [ ] Return paths: no splits under high-speed or sensitive signals
- [ ] Thermal reliefs / copper pours on power parts
- [ ] Fiducials, mounting holes and keep-outs in place
- [ ] Silkscreen readable: designators, polarity, pin 1, board name and revision
- [ ] 3D view checked for mechanical clashes

### Release
- [ ] Revision updated in the title block and on the silkscreen
- [ ] CI outputs reviewed (Gerbers, BOM, PDF)

## Notes / justified exceptions
