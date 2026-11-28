# Whoop flight controller

Presenter read-along · 2:30

Start the silent film at 0:00. Read each paragraph aloud when its timestamp arrives. The italic notes cue your eye.

## From airframe to board

### 0:00–0:11 · Under the canopy

*Start on the full quad. Look beneath the orange canopy.*

On this quad, opposite motors are sixty-five millimeters apart. The board under the orange canopy is usually the part I buy. I want to send my own to the board shop.

### 0:11–0:24 · The aircraft in Codex Desktop

*Find the four rubber mounts as the canopy lifts.*

I start in Codex with the Physical Engineering plugin’s Whoop reference. The motors and camera stay. Look at the four rubber mounts as the canopy comes off. My board has to land on those.

### 0:24–0:36 · The printable frame

*Follow the outside of the guards, then the propeller sweep.*

I also want thicker guards. Codex adds the plastic on the outside and checks each propeller through a full turn. All four still have eight tenths of a millimeter to spare.

### 0:36–0:49 · The original flight controller

*U1 appears first, then U2. At 0:44, look for the SPI schematic.*

Here’s the board Codex laid out in KiCad. The big chip is the flight processor. The little one beside it measures how fast the quad is turning. Let’s follow the link between them.

## Choosing the electronics

### 0:49–1:02 · From gyro to motor commands

*Follow the gold and blue clock trace. At 0:56, shift to the four edge pads.*

This clock trace runs from the processor to the gyro. These four pads at the board edge carry commands to the motors. The speed controller handles the current.

### 1:02–1:17 · Choosing the motor stage

*Read the 5.20 A point against the 5 A line. At 1:12, the 10 A choice appears.*

The first speed controller I picked was rated for five amps in a burst. In the manufacturer’s test, our motor and propeller drew five point two. I switched to a ten amp stage.

### 1:17–1:30 · The two-millimeter interference

*Find the red patch where the lifted camera dock crosses the propeller sweep.*

Why not stack the electronics higher? Lift the camera dock just two millimeters and its corner enters the volume swept by a propeller. That little patch of red rules it out.

### 1:30–1:46 · The revised frame and sled

*Follow the board’s four mounts, then the sled beneath the frame.*

The flight board stays on the four grommets. Codex puts the speed controller underneath and makes this removable sled for the battery, radio and video electronics. Now I can take the sled off to get at the wiring.

## From files to first power

### 1:46–2:03 · The board shop’s files

*At 1:53, look for the four drill holes. At 1:59, the placement list appears.*

Let’s open the files the board shop gets. Here are the four copper layers. Those four large unplated holes are the grommet mounts again, this time in the drill file. The placement list locates all twenty-six parts.

### 2:03–2:16 · The aircraft parts workbook

*Find FC01 on the aircraft sheet. At 2:08, move to the board’s own parts.*

In the aircraft workbook, the flight computer counts once. Its parts sheet names the exact processor, gyro, regulator and passives to order.

### 2:16–2:30 · First power-up

*Pause after “the props stay off.” Read the three planned checks distinctly.*

I’m quoting five prototypes. On the first one, the props stay off. I want the processor to answer the debugger, the gyro to register a turn, and four motor commands on the scope.
