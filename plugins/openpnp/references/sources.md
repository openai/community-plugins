# Source register and scope

Reviewed September 10, 2026. These primary sources explain native domain behavior; they do not certify this plugin, its bridge, or a physical machine. Runtime capabilities, the selected profile, and measured qualification determine executable scope. The workflow contracts in these references are plugin guidance.

- [OpenPnP setup and calibration](https://github.com/openpnp/openpnp/wiki/Setup-and-Calibration): modern Issues & Solutions guidance and legacy setup cautions.
- [Opulo V4.1 calibration procedure](https://docs.opulo.io/openpnp/v4-1/preflight/calibration-philosophy/): manufacturer-specific calibration sequencing and exceptions.
- [Native job processing](https://github.com/openpnp/openpnp/wiki/Job-Processing): native process, retries, pause/restart, and planning.
- [Asynchronous driver behavior](https://github.com/openpnp/openpnp/wiki/GcodeAsyncDriver): communication and motion-completion context.
- [Nozzle-tip calibration](https://github.com/openpnp/openpnp/wiki/Nozzle-Tip-Calibration-Setup): runout, offsets, and invalidation.
- [Bottom vision](https://github.com/openpnp/openpnp/wiki/Bottom-Vision): alignment and setting inheritance.
- [Vacuum sensing](https://github.com/openpnp/openpnp/wiki/Setup-and-Calibration_Vacuum-Sensing): part-state measurement.
- [Strip feeders](https://github.com/openpnp/openpnp/wiki/ReferenceStripFeeder): feeder geometry and material handling.
- [Centroid import](https://github.com/openpnp/openpnp/wiki/Importing-Centroid-Data): named importers and source conventions.
- [Panels](https://github.com/openpnp/openpnp/wiki/Panels) and [fiducials](https://github.com/openpnp/openpnp/wiki/Fiducials): board/panel relationships and registration.
- [Configuration implementation at commit 5bd404c](https://github.com/openpnp/openpnp/blob/5bd404cfc70f34103a3ca0fbb6b50c2b465f407c/src/main/java/org/openpnp/model/Configuration.java): source-level persistence reference; repeat verification for the actual deployment build.

Manufacturer and wiki pages can change. A profile should retain its chosen procedure/build receipt. Do not combine instructions from different hardware revisions or treat a later wiki feature as available in the pinned installer.
