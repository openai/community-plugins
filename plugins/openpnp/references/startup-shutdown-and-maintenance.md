# Startup, shutdown, and maintenance

Use the selected manufacturer's procedure and current machine profile. The skill cannot infer a service interval, lubrication method, or replacement part from generic OpenPnP support.

At startup, reconcile prior unresolved operations, confirm device/tool identities and occupancy, inspect the required air/vacuum/camera/fixture state, and home only through its qualified recipe. Preserve valid calibration; revalidate only affected dependencies or required scheduled checks.

For diagnosis, compare raw evidence across time: calibration residuals, image sharpness/lighting, vacuum readings, pick/release failures, mechanical changes, and material quality. Separate commanded state from actual measurement. Use supported active sensor/calibration tests with declared effects. Coordinate cleaning, tightening, refitting, focus, and replacement as physical steps where no qualified hardware does them.

For a local support package, follow [offline diagnostics and selective export](installation-and-adoption.md#bundled-local-diagnosis). Preserve paused/unknown work and the original journal; export is observational and does not require startup, cleanup or a machine-state change.

After a nozzle/camera/geometry change, invalidate the affected measurement chain and validate it before dependent production. OpenPnP's setup workflow is dependency-based and the relevant modern native solutions can help identify required steps. [Setup and calibration](https://github.com/openpnp/openpnp/wiki/Setup-and-Calibration)

At shutdown, reach the qualified stopping boundary, resolve held parts, park only along a valid path, preserve journal/report and intended settings, and follow the profile's pump/air/motor/disconnection behavior. Do not assume disabling every output is harmless while a part is held. Report final occupancy/position confidence and any restart/homing requirement.
