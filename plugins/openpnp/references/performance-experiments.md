# Throughput and quality experiments

Compare the same workload before and after one supported change. Retain baseline job/configuration/calibration/material revisions, package mix, operator interventions, inspected sample coverage, failed picks/retained parts, consumed components, and cycle time.

Use actual stage timing to choose a bottleneck. Planner order, tip changes, travel, settling, retries, and vision settings are different causes; changing an unrelated speed value is not an evidence-based experiment. OpenPnP's planner and job-order settings influence which placements are scheduled and may affect tip usage. [Job processing](https://github.com/openpnp/openpnp/wiki/Job-Processing)

Prepare a typed plan and preview affected shared settings. Keep the profile's motion/vision/retry limits. A calibration or qualification invalidated by the edit must be re-established before production. Test on the authorized boards and record independent quality outcomes through the available inspection path.

Report raw denominators and comparable results. A shorter run with fewer placed parts, more skips, worse inspection, or missing evidence is not a demonstrated improvement. Retain a candidate only when its acceptance criteria pass; otherwise restore the supported baseline and revalidate its affected dependencies.

If the runtime does not support the desired setting or inspection, complete the analysis and proposed experiment with that gap stated. Do not weaken durability, sensing, or acceptance thresholds to meet a timing target.

For the separate [sustained simulator qualification recipe](sustained-simulator-qualification.md), report the actual advertised native job order. Its ordered 10,000-placement grid uses the public `Unsorted` order to avoid repeatedly optimizing all remaining placements under `NozzleTips`. This changes the workload recipe; it is not evidence that a previous interrupted run passed or that the default profile/physical machine became faster.

## Current simulator measurement

Development measurements of Bridge `970e4635` with native runtime `810773c1` used three alternating pairs of fresh JVMs, each with 20 warmup and 100 measured placements. The accelerated simulator took a median 22.782 seconds through Bridge versus 4.040 seconds directly per 100 placements. Median paired overhead was 463.90%, exceeding the proposed 5% target. A separate diagnostic copy measured required journal force calls at 76.59% of job elapsed time. All durability barriers remained enabled. These short simulator measurements do not predict physical throughput.

The optional developer benchmark requires the full source checkout. Its script is `scripts/openpnp-benchmark-packaged.py`; the repository report is `docs/openpnp-performance-analysis.md`. Those development files are not included in a standalone installed plugin. Continue the installed workflow above with the connected runtime's advertised tools and retained measurements.
