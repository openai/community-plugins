# Four tenths of a millimeter

Physical Engineering in Codex · Presenter runbook · 6:24

## 0:00–0:20

*On screen — Two guard profiles, then the bench*

One line of CAD makes this guard forty percent thicker. On this quad, that’s four tenths of a millimeter. Before I print a whole frame, I’d like to bend a sample of each guard, let go, and see whether the plate underneath rings differently.

## 0:20–0:47

*On screen — Codex opens the supplied Whoop*

Here’s my starting point in Codex. Physical Engineering supplies the sixty-five millimeter quad. The motors and camera are already placed. I’m asking for a new printable frame around those parts. The propellers stay, which gives us a decision to make about where the extra plastic goes.

## 0:47–1:00

*On screen — The guard radius in frame.py*

Look at the inside edge. If I add the plastic there, I’ve just spent half my clearance between the prop and the guard.

## 1:00–1:20

*On screen — The frame STL rotates. The prop sweeps the bore.*

Codex grows the outside instead. Here’s the frame STL. The check reopens the exported solid and sweeps each blade through a full turn. All four still clear by eight tenths of a millimeter. The whole frame gets eight tenths wider.

## 1:20–1:44

*On screen — The 5IN1 part number and the two board sections*

That flight controller comes in two versions on the same product page. Follow the part number. We picked the five-in-one. Its mounting holes line up, but the modeled board is too thick. The manufacturer specifies point eight millimeters. Codex corrects the board and brings the components on top down with it.

## 1:44–1:54

*On screen — Motor lead beside controller spec*

The motors ship with plugs. This controller takes soldered leads. I want that in the workbook before someone orders four motors.

## 1:54–2:17

*On screen — The empty fixture project. Two arc samples share a plate.*

How do I compare the guards without crashing two aircraft? I give Codex the dimensions of the curved section and an empty folder. It designs a replaceable sample, a plate and the yellow stop. Both samples sit on the same pins. I can swap one without disturbing the sensor.

## 2:17–2:40

*On screen — The stop gap, then four printable parts*

I’d push the free end against the stop and let go. The stop sits two millimeters from either sample. The thicker wall may take more force to get there, but I can give both the same starting displacement. Four print files give me the plate, the stop and both samples.

## 2:40–2:56

*On screen — The shifted stop reads 2.50 mm*

Watch the thick sample when I shift the stop. The gap goes from two millimeters to two and a half. That’s twenty-five percent more travel for just one of the samples. The drawing check catches it before I print.

## 2:56–3:09

*On screen — The independent PCB plot, then the fitted green board*

That plot came from a second, empty Codex project. Same board brief, different routing. The green board coming up is the one fitted to this case.

## 3:09–3:32

*On screen — The accelerometer schematic and pull-ups*

The chip in the middle measures acceleration on three axes. Four wires run back to the host. Two supply power and ground. The clock and data lines each get a pull-up resistor. That’s enough to power the sensor and read the plate’s motion after a release.

## 3:32–3:52

*On screen — The board outline, then the animated SDA route*

The case gives us four fixed mounting holes. The cable enters at the front. Follow the data line from pin three. It drops to the back, comes up through a second via and lands on the sensor.

## 3:52–4:10

*On screen — Two connector hold-downs at the board edge*

Every pad was connected. The error was under the connector’s metal feet. They were too close to the cut edge. Codex moves the connector a quarter millimeter inward. Both feet clear the rule, and the routed board passes KiCad’s checks.

## 4:10–4:22

*On screen — The board seats in the case. The lid clears.*

The board bolts down. The lid closes. This is where I’d be tempted to hit Print. Look through the front wall. Can I get a plug in there?

## 4:22–4:47

*On screen — A yellow plug gauge passes through both openings*

The yellow block is the space the plug needs on its way to the socket. Its top edge hits the plastic. There’s room on both sides, so Codex makes the opening taller. Run the same gauge again. It clears. On the first print I’d still want to feel the real plug seat.

## 4:47–5:14

*On screen — The closed station, then the failed mounting drawing*

I can reach both mounting screws with the lid on. Here’s a mistake I put in on purpose. The drawing says sixty-four millimeters between those holes. I gave it sixty. Nineteen general checks passed. Two clean through-holes, both in the wrong place. Reading their centers back from the exported part caught it.

## 5:14–5:34

*On screen — The lid lifts and the assembly handoff opens*

With the holes fixed, the recorder stays put when I change the coupon. If you took over the build here, you could print the plastic and send the copper layers and drill files to a board shop. You’d still have a purchasing question.

## 5:34–5:50

*On screen — Grommets and dock screws, then fitted electronic parts*

What comes in the box? The controller includes four grommets. The dock needs four longer screws, bought separately. The next tab lists the parts to populate the sensor board, down to the two pull-ups.

## 5:50–6:05

*On screen — Results from the clean rebuild*

I handed the source to a fresh Codex run. It rebuilt the station model, both coupon meshes and all three assembly workbooks. Those files matched the originals byte for byte.

## 6:05–6:24

*On screen — Alternate the specimens. Finish with both products in frame.*

I’d start with five releases of the same coupon. That tells me how much scatter comes from my hand and the fixture. Then I’d swap in the thicker one and repeat. Hand me those traces without the labels. Can I tell which guard was on the plate?
