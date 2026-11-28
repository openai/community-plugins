# Four tenths of a millimeter

**Physical Engineering in Codex · Presenter script · 6:24**

Play the [silent stage film on PR #61](https://github.com/openai/community-plugins/pull/61) and begin on the first frame. Keep this script on a second screen. Each time in the left margin is a speaking cue. For a live run, use the [copyable prompt deck](Prompt-runbook.html) or the [plain-text prompt runbook](Prompt-runbook.md).

## The engineering question

Hold on the two guard walls. Ask the audience what they would need to see before choosing one.

### 0:00 · The frame and the experiment

**0:00**  These two propeller guards differ by four tenths of a millimeter. Which one would you put on an indoor quad?

**0:09**  I want more than a render. Let’s design the frame, an experiment to compare the guards, and the circuit board that can measure the result.

### 0:20 · Start in Codex

**0:20**  In Codex, I select Physical Engineering and open the supplied sixty-five millimeter quad. The frame, motor locations and purchased-board envelopes give us a starting point.

**0:32**  The new brief has three jobs. Revise the guard, check the parts we would actually buy, and build a tabletop instrument for a controlled test.

### 0:47 · Cut a real print file

**0:47**  Here’s the edit in the actual CAD source. Keep the bore fixed. Grow the outside of each ring until its radial wall measures one point four millimeters.

**0:59**  Codex exports the frame STL, then reopens the STEP. All four walls meet the drawing. A full turn of each propeller still leaves zero point eight millimeters.

**1:13**  Across the whole frame, the change costs zero point eight millimeters in width.

## Design the experiment

Point to the controller part number, then follow the physical chain from the coupon to the plate.

### 1:20 · Let a supplier change the CAD

**1:20**  Now follow the flight controller from the workbook back to its manufacturer. This is the five-in-one variant. It has a twenty-six millimeter square mount.

**1:32**  The model’s substrate is zero point nine five millimeters thick. The supplier specifies zero point eight. Codex changes the board model and checks the exported solid again.

**1:45**  The motor also comes on a connector. This controller takes soldered motor leads. That decision belongs in the parts list before anyone buys a set.

### 1:54 · Build the test fixture

**1:54**  A thicker rim looks convincing on screen. How would we compare it? I give Codex an empty project and ask for a fixture it has to design.

**2:06**  Both coupons use the same inner arc. They share one locator on the plate. I can swap the sample and keep the sensor in the same place.

**2:18**  The removable gauge sets a nominal two millimeter tip displacement. On the bench I would bring the coupon to the stop, release it, and record the plate response.

### 2:29 · Put the print files on the table

**2:29**  These are the printable parts Codex generated. The two arc sections are separate meshes. The fixture and gauge have their own editable geometry.

**2:40**  The drawing check finds the coupon walls, the stop and the mounting holes in the exports. That establishes the test geometry. Repeated releases will tell us how consistent the test is.

## Follow the signal

Trace connector pin three to the sensor. Pause on the cable passage and let the audience find the collision.

### 2:56 · Design the measurement board

**2:56**  Now we need an instrument. The custom board bolts into the enclosure and carries a three-axis accelerometer. The cable supplies power and takes measurements back to a host.

**3:09**  Here is the actual schematic. We chose an ST accelerometer on a three point three volt I-squared-C bus. The address pin selects hexadecimal eighteen.

**3:20**  Follow the two signal nets. Each has a pull-up to the supply. Local capacitors sit close to the sensor power pins. Those choices show up in the board BOM.

### 3:32 · Follow the copper

**3:32**  This is the editable KiCad layout. Four mounting holes come from the case. The cable connector needs the front edge. Those two constraints leave the middle for the sensor.

**3:45**  Follow the data line from connector pin three to the sensor. The first route connected every pad. Two metal hold-downs still missed the copper-to-edge rule.

**3:59**  Move the connector inward a quarter millimeter and reroute. Clearance reaches zero point six. KiCad reports zero violations and zero unconnected pads.

### 4:10 · Four holes line up. The plug hits.

**4:10**  Put the populated board into the housing. The four mounting holes line up. The lid closes. Now try bringing the cable straight through the front wall.

**4:22**  The old opening is too low. In the exported solid, the plug envelope overlaps twenty-eight point seven cubic millimeters of plastic. The cable needs a different opening.

**4:36**  Codex changes that wall and exports the base again. Run exactly the same gauge. The overlap drops to zero.

## Make it reproducible

Show the installed recorder, the purchasing sheet and the clean-source rebuild. End on the unrun experiment.

### 4:47 · Make the products meet

**4:47**  Back on the fixture, the sensor attaches from outside the case, with its lid on. The connector faces the cable exit and the coupons remain accessible.

**5:00**  That sixty-four millimeter mounting pitch has its own check. A deliberately wrong sixty millimeter version passed nineteen general checks, then failed the drawing and was refused.

**5:14**  The working assembly is the repaired one. You can open its model, inspect an individual part, or print just that part’s mesh.

### 5:22 · Hand it to another engineer

**5:22**  The handoff has three paths to follow. Print the mechanical parts. Source the actual purchased parts. Inspect the original schematic, routed board, Gerber layers and drill file.

**5:34**  The workbook keeps the controller’s included grommets separate from the longer dock screws. Exact part numbers and supplier links sit next to the items. Availability is dated.

### 5:50 · Repeat the build. Then run the experiment.

**5:50**  The last test is to put the handoffs in new folders and rebuild. Read the dimensions again from the new exports. Open the new workbooks and compare them with their baselines.

**6:04**  What would I take to the bench? The two coupons, the fixture, and the board. Fit the connector, power the sensor, then record repeated releases on the same plate.

**6:17**  That first real vibration trace is the next piece of evidence.

## At the controls

Start the silent film and your timer together. Pause when a question comes in. Resume from the next printed cue. Seek to **3:32** for the routed board, **4:10** for the plug collision, or **5:50** for the delivered-source rebuild. For a solo rehearsal, use the [narrated edition on PR #61](https://github.com/openai/community-plugins/pull/61).
