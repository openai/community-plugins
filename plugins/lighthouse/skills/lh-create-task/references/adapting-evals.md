# Adapt an existing evaluation deliberately

Use this reference when a benchmark, catalog or packaged world supplies the
starting point. Preserve useful work and test its evaluator; catalog inclusion,
a runnable image and a passing reference solution establish different things.

## Inspect the complete contract before copying the package

Pin the source revision and inspect the request, all learner context, environment
recipe, tool implementation, adapter configuration and verifier. Record missing
files, inaccessible assets, licenses and answer-bearing material inspected by
the curator. A source bundle can omit transitive dependencies even when its own
file tree is complete. Keep source answers out of the new learner environment.

Trace what the selected entrypoint actually supplies. Optional prompt context,
formatting rules, plugin instructions and tool permissions can change the task.
Resolve documentation/code disagreements against the executed configuration;
leave unresolved behavior unknown. Never recover missing requirements by putting
private rubric details into learner instructions.

Describe actual state owners and operations. Seven named MCP endpoints might
share one database; several applications might be stdio processes in one
container. A JSON model called a spreadsheet is different from an XLSX workbook,
and a service-shaped simulator does not establish the production service's
behavior. Record the distinction before selecting the smallest useful setup.

## State what the new task preserves

Keep a short mapping in the task's existing review record:

| Source behavior | New implementation and evidence | Changed or untested semantics |
| --- | --- | --- |

A faithful port preserves the relevant inputs, application behavior and grading
meaning. A synthetic adaptation uses newly authored assets and may change those
contracts. Label it accordingly; do not report its score as an upstream result.
Original synthetic material can borrow an architectural pattern without copying
a restricted corpus. Record reuse rights for any copied material separately.

Preserve the cognitive work as well as the output shape. Related tasks should
require distinct investigation or decisions, not just substitute names and
numbers. Define each scenario's immutable base and changes, and reset it
independently unless a sequential episode is explicitly intended. Use
[complex environments](complex-environments.md) for the native lifecycle.

## Challenge the executable grading rules

Map the disclosed request to actual assertions using
[construction](construction.md). Check the behaviors the source claims to test:

- Recompute stated formulas independently and compare units, rounding and edge
  cases with the checker. A conflicting formula is an authoring defect.
- For incremental or recovery work, change the inputs between operations: inject
  a late row, correction or failure. Repeating an unchanged run is insufficient.
- Check required key/row coverage, types and finite values before numerical
  scoring. Record metric direction; zero error from missing output is not success.
- Exercise the claimed behavior through its real boundary. Source tokens,
  self-reported counts or output file size do not prove execution or correctness.
- Keep scored failure, unavailable evidence and pending judgment distinct. A
  deterministic pass cannot satisfy a required but unperformed judgment.

Treat source inspection as a hypothesis. Execute reference, alternative,
counterexample and weakened-checker controls in the new package before claiming
the issue occurs there. Retain the source finding and the measured result as
separate evidence. Repair the new task rather than carrying over known defects
to preserve superficial parity.

## Report the study and execution separately

For a source sample, retain exact membership, selection rationale and exclusions.
Report how many cases were inspected, adapted, qualified, run and regraded.
Count worlds or scenario families as well as tasks: related cases share context
and do not constitute independent evidence of generalization. A purposive sample
supports design lessons, not a population failure rate.
