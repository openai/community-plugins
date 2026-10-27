# Independent skill decision evaluation

These 42 offline cases exercise the fourteen skills with happy-path, failure, and recovery observations. They are normalized raw fixtures, not a recording of current MCP outputs. Candidate tool names must still be checked against an actual runtime for end-to-end evaluation.

## Run a decision exercise

1. Choose a case from `catalog.json`. Give an independent evaluator only that case JSON, the named `SKILL.md`, and linked runtime/domain references. Do not supply the rubric or this catalog's scenario labels.
2. Ask it to handle the case request from the supplied observations and provide its next actions, required evidence, proposed tool-call order, and user-facing result. Permit no hardware contact, external writes, or actual control calls. Additional required measurements should be reported as missing, not invented.
3. A separate assessor reads the matching rubric and evaluates the actual decisions, claims, source provenance, and proposed call sequence. Record case/skill hashes, evaluator version, actual output, assessor rationale, and pass/fail. Do not count a correct phrase in an otherwise incorrect action sequence as a pass.
4. Store results separately from these versioned inputs. A test validating fixture format or local links is a packaging check, not a model-behavior result.

## Advance to runtime evaluation

Use the live tool schemas and an isolated deterministic or native OpenPnP simulator. Capture actual tool calls/results, operation IDs, capability origins, and resulting state. Verify the cases' meaningful invariants against that evidence, with no physical drivers accessible. Adapt raw fixtures to the selected simulator explicitly; do not pretend these normalized facts are tool responses.

Actual hardware acceptance remains separate and requires the qualified machine profile, operator procedure, inspection, and fault tests. Passing offline cases, simulator tests, or skill metadata validation cannot establish physical readiness.
