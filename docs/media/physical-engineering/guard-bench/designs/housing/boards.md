# Bench accelerometer interface

The custom sensor board mounts on four posts in a PETG housing. The underside of the board sits 5.00 mm above the bench datum. The four Ø2.30 mm mounting holes are centered at X = ±14.00 mm and Y = ±8.00 mm. Four M2 × 4 mm screws retain the board. The board assembly installed in this model comes from the accompanying ECAD design. The [populated STEP](components/sensor_board.step) and [mechanical readback](integration.json) use installed coordinates.

J1 is a four-position, side-entry JST SH header. Its cable leaves toward −Y, through a rounded opening centered at X = 0 and Z = 8.20 mm. The window measures 13.00 mm wide by 7.40 mm high at its centerlines. The top of the board must leave at least 3.00 mm of room when the lid is closed or raised vertically, after removing the four lid screws.

The cable approach model uses a 7.0 × 2.8 mm rectangular gauge to 0.05 mm ahead of J1’s foremost material. The flanged SHR-04V-S-B housing measures 7.0 mm across; the manufacturer’s side drawing gives its 2.8 mm section. A separate 9.0 × 4.0 mm clearance volume reaches the front edge of the substrate. The report separately extends the bounding gauge to 0.05 mm before the molded mouth. It can touch a protruding solder tab there because it omits the keyed shape of the plug. Physical insertion, finger access, cable routing and strain relief require the selected harness. [JST SH family drawing](https://www.jst-india.com/downloads/series/eSH_%2821-03-24%29.pdf)

The two external ears retain the 64.00 mm mounting pattern of the earlier enclosure. Both Ø3.40 mm through holes sit on Y = 0 at X = ±32.00 mm, through 3.20 mm of PETG. The fixture determines which screw length and thread engagement to use.

## Electrical source

Open the [KiCad schematic](boards/accelerometer/bench-accelerometer.kicad_sch), [native PCB](boards/accelerometer/bench-accelerometer.kicad_pcb) or [schematic PDF](boards/accelerometer/schematic.pdf). The nominal PCB stack is 1.60 mm. The full KiCad STEP contains separate solids. In local coordinates the PCB solid spans Z=0.000–1.510 mm, copper and vias span −0.035–1.545 mm, and pads span 1.510–1.550 mm. Component seats start at 1.595 mm. The native design specifies 1.600 mm finished thickness but provides no explicit factory stackup documenting the remaining finish or solder-mask thickness. Measure the fabricated board and confirm the purchased stackup. The JST reference model uses manufacturer and land-pattern dimensions for mechanical integration.

The [board-level BOM](boards/accelerometer/kicad-bom.csv), [machine placement list](boards/accelerometer/placements.csv), [routed copper STEP](boards/accelerometer/bench-accelerometer.step) and [KiCad DRC report](boards/accelerometer/drc.json) accompany the native design.

The colored assembly distinguishes bare copper, pads, vias and component bodies for inspection. The display palette is declared in the editable recipe. Production solder-mask appearance depends on the fabricator.
