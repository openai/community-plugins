# Tooling and pneumatics

Resolve the physical nozzle, attached tip, known occupancy, package compatibility, tool dimensions, working heights, and vacuum/air mapping before changing anything. Manual and automatic tip changes have different physical continuations; a missing changer must not be simulated as installed.

Use a supported typed configuration plan for nozzle/package limits and actuator mappings. A changed tip, offset, or tool identity can invalidate calibration. Let the profile name the needed routine, including any automatic native calibration that follows a tip change.

For part sensing, record baseline and part-on/part-off measurements with their device/source, sample context, thresholds, and uncertainty. Values must distinguish the intended states; there is no universal vacuum number suitable for every nozzle/package. Active part verification can change vacuum state, so it belongs in an effect-declared recipe.

OpenPnP vacuum sensing uses measurements to identify successful picks and retained parts; the selected sensing method and calibration affect how values should be interpreted. [Vacuum sensing](https://github.com/openpnp/openpnp/wiki/Setup-and-Calibration_Vacuum-Sensing)

A bounded handling test records consumption and nozzle occupancy whether it is part of production or a diagnostic. After an uncertain release, do not report an empty nozzle or free pocket without observation. On pause/shutdown, preserve the profile's behavior for a held part instead of blindly turning off all actuators.

For the advertised controlled simulator source, use the [native sensing workflow](vacuum-sensing.md), its revision-bound asynchronous tools, active-probe prerequisites and sticky fault rules.
