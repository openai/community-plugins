# Validate a GitOps correction

`devsecops-review validate-correction` validates the actual proposed patch in a
private repository made from a full frozen base commit. It uses the same Helm,
Kustomize, schema and policy checks as ordinary review, then checks the rendered
changes against an explicit correction contract. It never delivers a change.
Every target in the contract-bound profile is reviewed, including targets that
share values or bases; a command-line selection cannot narrow this coverage.

```sh
python scripts/devsecops_review.py validate-correction \
  --repo /absolute/application-repository \
  --base FULL_FROZEN_COMMIT_SHA \
  --profile /absolute/trusted-review-profile.json \
  --contract /absolute/trusted-correction-contract.json \
  --patch /absolute/model-proposal.diff \
  --schema-location /absolute/local-kubernetes-schemas \
  --output /absolute/new-correction-result
```

The contract follows [correction-contract.schema.json](../schemas/correction-contract.schema.json).
It contains `schema_version: 1`, an `id`, the exact `base_commit`, the SHA-256 of
the trusted profile's bytes, `allowed_files` (existing repository-relative paths),
and `allowed_rendered_changes`. Each rendered change selects an exact resource
identity and allowed RFC 6901 pointer subtrees. For example, a metrics repair may
permit only `/spec/ingress/0/ports/0/port` on
`networking.k8s.io/NetworkPolicy/demo/mission-metrics`, with only that policy's
source or values file editable. It does not authorize changes to other policies,
images or workload resources. The canonical factory must supply this contract as
an immutable input to its native correction request.

The contract must be outside the candidate checkout. The native correction skill
must preserve it and the trusted profile; widening either to obtain a pass is not
a valid correction. All selected base inputs are frozen without checking out or
changing the source repository. Patch application occurs only in private scratch.
Candidate scripts, hooks, filters and CI jobs are not executed. Explicit trusted
tool executables and local schemas must be outside the candidate checkout.

This initial contract supports in-place text edits. File creation, deletion,
rename, mode changes and binary patches are unsupported. The complete selected
rendered resource inventory and every image field must remain unchanged. Changes
outside the declared pointer subtrees fail validation. Equal-length lists are
compared at every index, so reordered peer selectors remain visible. A length
change affects the whole list and cannot fit an allowed single-field pointer. A
comment-only patch fails correction validation even if ordinary static review
passes. Missing schemas, tools, unsupported render inputs or unbound resource
scope remain `unknown`.

The output includes:

- `correction.html`, `correction.md`: the correction outcome, independently of
  the underlying static result, with delivery and recovery stages shown explicitly.
- `correction.json`: source/base/profile/contract identities, exact patch digest,
  frozen base and patched-tree digests, field-scope receipts, decision and gaps.
- `proposal.patch`: the exact captured patch, retained in a private output directory.
- `rendered-before.json`, `rendered-after.json`: the exact normalized resource
  bodies used by the scope check, captured during that same render and bound by
  `correction.json` to the frozen inputs. These private files may contain manifest
  secrets; they are not embedded in the readable reports. They provide the
  `rendered_before` and `rendered_after` inputs for
  [independent recovery verification](interfaces/runtime-recovery.md).
- `report.html`, `report.md`, `report.json`: the underlying static review. Its
  synthetic Git revision identifies the frozen scratch repository; the original
  source commit is recorded in `correction.json`.
- `correction-manifest.json`: written last, binding all artifacts and the terminal
  correction outcome. A static review manifest alone is not correction success.

Render exports cover every selected target, have unique resource identities,
and are bounded to 1,000 resources and 16 MB per side. Missing, ambiguous or
oversized render evidence stays incomplete. Full bodies are stored with private
permissions and directory-anchored writes; an output-path replacement prevents
a terminal correction manifest. Existing Python callers may continue using the
three-item `validate(args)` result. Request export with an empty
`rendered_artifacts` mapping and pass it unchanged to `write_outputs` to retain
the byte-bound recovery inputs.

Exit codes are 0 for complete local correction validation, 1 for a completed
validation failure, and 2 for incomplete/error. Use the correction decision as
the final local gate. Delivery, controller parity, admission, policy enforcement
and functional recovery require separate evidence; none is implied by exit 0.
