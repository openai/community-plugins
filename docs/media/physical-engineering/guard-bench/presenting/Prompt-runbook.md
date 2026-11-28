# Run the guard-bench demo in ChatGPT Desktop

The audience question is which of two guard walls you would choose for an indoor quad. The aircraft sets the dimensions. The tabletop fixture makes the comparison possible. The original accelerometer PCB gives the test a way to observe the plate response. Both designs are digital prototypes.

## Set up before going on stage

1. Open a writable checkout of [community-plugins PR 61](https://github.com/openai/community-plugins/pull/61). A reviewer can use `gh repo clone openai/community-plugins`, enter that new directory and run `gh pr checkout 61`. The source companion is `docs/media/physical-engineering/guard-bench/`. The [companion README](../README.md) has the portable source and replay command.
2. In the desktop app, switch to Codex and open that local project. Select the PR version of Physical Engineering, **0.3.0-preview.2**, in the composer. Use the prepared plugin CAD runtime and KiCad **9.0.2** with its matching libraries. The companion README includes the native and container choices. The first setup needs network access. Preload runtimes and source documents before the audience arrives. [ChatGPT Desktop and Codex](https://help.openai.com/en/articles/20001275-chatgpt-work-and-codex)
3. Open [the prompt deck](Prompt-runbook.html) alongside Codex. For an exact-source stage run, choose **Copy source replay**, paste once and send. The runner exports into a fresh directory from the native recipes and board project. Its receipt is `replay.json`. The deck also contains individual briefs for fresh engineering. Those turns involve real CAD and PCB design time and can yield different valid details.
4. For a timed six-minute presentation, open [the silent stage film on PR #61](https://github.com/openai/community-plugins/pull/61) and the [read-along script](Presenter-runbook.pdf). For audience questions, pause and open the real local handoffs. The [narrated edition on PR #61](https://github.com/openai/community-plugins/pull/61) has the same frames, chapters and timing.

## Two practical routes

**On a clock.** Start the source replay before the session. Open the live project with chat and CAD side by side. Play the stage film to pace the explanation, then return to the finished replay and open a file the audience chooses. The film edits several genuine engineering sessions together and compresses build time. The two tabletop coupons have never been physically deflected.

**Live authoring.** Send the numbered briefs in order. Wait for each output before starting a dependent step. The fixture and electrical design can proceed independently after their mounting interfaces are established. A fresh bench fixture took about eight minutes on the prepared demo host. A clean run may choose different geometry or PCB routing. Use the new run’s reports on stage. For a deliberate digital failure, keep the 64 mm drawing fixed and change only the candidate’s bore pitch to 60 mm. Use the mount and cable prompts to follow the failures back to physical parts.

## Reproduce the reviewed result


Use @Physical Engineering in this writable local Codex project. Rebuild the exact guard-bench study from the provided editable sources. Find the companion directory containing replay.py, ecad/ and designs/. Read its README and find the matching Physical Engineering plugin and prepared CAD runtime. Show which source folders are the inputs. Choose an unused output directory outside the sources. Run replay.py there, using KiCad 9.0.2 and its matching libraries. Use the companion’s documented --kicad-image option if the provided Docker image is how KiCad is installed. If the companion is outside a repository checkout, pass --plugin with the matching plugin directory.

Open the newly generated station/assembly.step in Codex. Show the individual coupon STLs, the routed board and the newly exported Gerbers and drill data. Open the rebuilt aircraft BOM and the consolidated purchasing workbook. Walk me from the controller part number to its manufacturer and from connector pin 3 to the sensor. Read replay.json and the generated measurement reports. Show the passing measurements and the deliberately rejected edge-clearance control. Summarize what the new run actually verified and identify the first checks that require real hardware.

## Live authoring prompts

### 1. Open the engineering workspace · 0:00

For the repeatable live route, use the top-right copy button after setup. Use the chapters below for independent authoring.

```text
Use @Physical Engineering. Inspect this project for the guard-bench companion and the matching plugin. Report the plugin version, CAD runtime, KiCad CLI version and available 3D viewer. If the documented runtime is missing, follow the companion setup. Check whether the native matching KiCad libraries or the prepared local Docker image are available. Locate the editable recipes, PCB source and dated procurement register. Reserve a new output directory so prior runs remain available.
```

The plugin version and both engineering runtimes appear in the chat. Show the audience the input directory before opening a result.

### 2. Revise the airframe · 0:20



```text
Use @Physical Engineering. Make the next unused physical-demo-NNN project and show it is empty. Initialize editable Whoop 65 and sensor enclosure recipes from the supplied plugin references. State what each already contains. On the quad, change the four guard walls from 1.00 to 1.40 mm with the 16.30 mm inner radii, each propeller and the 65 mm motor diagonals fixed. Update the frame specification in the BOM. Write a source-level drawing check for the actual cylinders in the exported STEP, measure the overall width, check all four swept disks and export the printable frame STL and colored viewer. Open the new frame in Codex. Keep the interface measurements for the fixture brief.
```

Editable frame source, print/frame.stl, 1.40 mm walls and four nominal 0.80 mm tip clearances. Ask which dimension should stay fixed when adding material to a propeller guard.

### 3. Source the flight hardware · 1:20



```text
Follow the controller, motors, props, camera and battery from the flying assembly into a procurement workbook. Use manufacturer sources to resolve exact variant and MPN. Record the dated supplier page, mounting, connector or wire terminations, quantity, and included accessories. For the 5IN1 01040016_2, measure the PCB substrate on the exported STEP against the documented specification. Correct the model and its dependent interfaces if needed, rerun the build and show its before-and-after measurement. Separate the manufacturer identity, a candidate alternate seller, unverified fit assumptions and current orderability. Link every supported purchase decision. Do not place an order.
```

The reviewed flight-controller substrate changed from 0.95 mm to 0.80 mm. The motor lead termination requires an explicit build decision. Click one MPN so the audience can read a supplier specification. Stock may have changed.

### 4. Give the recorder a mounting drawing · 1:50



```text
In the editable enclosure recipe, design two external mounting ears. Keep the lid removable and join each ear to the base. The fixed drawing requires vertical Ø3.40 mm through holes at X = −32.00 and +32.00 mm, Y = 0. Add a STEP-based drawing check. Try a separate candidate with a deliberately wrong 60 mm pitch and show the rejection. Repair it to the 64 mm drawing, rebuild and export the accepted base and lid. Keep the mounting STEP for the fixture and board work.
```

The nominal mounting centers stay available as the enclosure and electronics evolve. Return to the original failed and corrected drawing results near the end of the film.

### 5. Author the guard test fixture · 1:54

This is the original fixture prompt run in Codex Desktop. The recorded run took 7 min 40 sec on the prepared host.

```text
Use Physical Engineering in this local Codex project. Design an original printable bench fixture to compare the two guard-wall designs from the indoor quad. Its purpose is to let me deflect and release one guard coupon at a repeatable displacement while a tabletop accelerometer enclosure bolts to the same plate. The quad is unpowered. We want a future measurement of the plate’s vibration, so distinguish measured CAD dimensions from any physical response.

Start a new, empty guard-bench-NNN folder. Use the existing revised Whoop and enclosure as dimensional inputs only. Author the fixture geometry from a blank recipe. Include replaceable curved guard coupons with the same inner radius and height as the frame and nominal 1.00 and 1.40 mm walls, a positive locator for each coupon, and a removable stop or gauge for a defined 2.00 mm deflection. Design a practical way to secure the enclosure at its 64.00 mm mounting pitch. Keep enough space to install it and remove the lid. Tell me how a person would use the setup and which comparisons it supports.

Use the plugin to build a complete editable handoff, with individual printable STLs, STEP, a colored assembly and formatted BOM including hardware. Add an independent geometry check for the actual exported coupon walls, reference interface, stop travel and sensor mounting centers. Rebuild from the delivered source in a clean folder and verify the results. Open the assembled model in Codex.
```

An empty source folder, editable fixture, two printable coupon variants and a measured 2.00 mm stop gap. Show the chat confirming zero starting files. Compare the four delivered print meshes.

### 6. Design the accelerometer PCB · 2:56

This repeats the electrical brief used for the independent Desktop run. The tool-location sentence is portable.

```text
Design an original accelerometer PCB for the guard-test plate. Start the next unused bench-board-NNN folder empty, and create the schematic and routed board there in KiCad. Use the manufacturer datasheets to choose and check the parts. Use the enclosure’s 34 × 22 × 1.6 mm board envelope and four Ø2.30 mm nonplated holes on 28 × 16 mm centers.

Use an ST LIS3DHTR powered from a regulated 3.3 V host, configured for I²C at address 0x18. Bring GND, 3.3 V, SDA and SCL to a front-facing four-pin JST SH side-entry connector. Include the necessary pull-ups and decoupling and an accessible data-ready test point. Keep copper at least 0.50 mm from the board edge. Author the electrical design, placement and traces from the brief.

Run KiCad ERC and DRC, including connectivity and schematic parity. Fix issues and report the actual counts. Trace SDA from the connector to the sensor. Export the editable design, BOM with part numbers and manufacturer links, pick-and-place, Gerbers, drill data, schematic PDF and a readable board view. Open the board or its actual plot in Codex. Use the prepared KiCad 9.0.2 environment for this project. Record the version and use a new work directory. No physical board has been tested.
```

A native schematic and PCB, actual traces and plated/nonplated drill outputs. Check the ERC/DRC reports and schematic parity for this run. Follow one signal from J1 to U1. A fresh layout may use a different trace length.

### 7. Fit the actual board and plug · 4:10



```text
Use the actual populated KiCad STEP from the accepted board. Register it to the four case standoffs and locate the side-entry connector from that geometry. Confirm lid clearance, board hole centers, screw clearances and the intended cable approach. Read the manufacturer drawing for the mating plug housing. Construct a nominal insertion gauge, move it along the approach through the existing front wall and measure the solid intersection in mm³. Revise the printable front opening as needed and use the identical gauge for the second measurement. Export the new base, mount it on the actual fixture in place of its envelopes, and show the combined assembly. Recheck fastener engagement, coupon-to-stop distance and clearance for lid removal. State the print and cable trials still needed.
```

The reviewed project measured 28.70 mm³ of overlap in the old wall and zero with the revised opening. Hold on the original intersection before opening the revised case.

### 8. Reconcile the BOM and release · 5:22



```text
Reconcile the aircraft BOM, fixture, recorder enclosure and original PCBA into a formatted offline workbook and CSV. Preserve counts, MPNs, physical units, manufacturer and supplier URLs and sourcing dates. Separate installed hardware, reusable bench tools, included fasteners, optional parts and alternate coupon variants. Count the assembled PCB once, with its procurement and placements traceable. Show one purchasable component, one print and one deliberate exclusion. Rebuild the accepted source in fresh directories. Compare STEP measurements, print meshes, both BOM formats, KiCad placements and fabrication output, and report the checks that actually ran. Open the combined assembly, workbook and one raw measurement file in Codex.
```

A reviewer can navigate the new outputs and work backward from a BOM line, printed mesh or copper route. End on the parts you would take to the bench and the measurement you would make first.
