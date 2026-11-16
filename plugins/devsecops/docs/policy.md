# Trusted profiles and finding policy

The profile is versioned data. It selects the review context and gate policy;
it does not authorize deployments, tool execution, network access or a compliance
claim. The [profile schema](../schemas/profile.schema.json) and runtime semantic
checks define the accepted input. The [rule catalog](rules.md) names supported
deterministic rules.

## Select the trusted version

An explicit profile outside the candidate repository is a trusted local input.
For a profile inside the repository, the engine reads the regular tracked file
from the resolved baseline commit. Candidate edits, deletion or symlink
replacement do not alter the profile used for that review. Reports retain the
profile origin, ID, revision and SHA-256. Keep approval and access control around
external profiles and approved baseline updates in the owning platform process.

## Configure the finding gate

The default gate fails on `introduced` or `worsened` deterministic findings at
effective severity `high` or `critical`. Resolved findings are retained but never
gate. The gate can include `preexisting` findings when a team intends to block
on existing issues as well.

This policy fragment illustrates a stricter review and one severity override;
it is configuration syntax, not a recommended customer policy:

```json
{
  "require_image_digest": true,
  "allowed_registries": ["registry.example.invalid"],
  "required_labels": ["team"],
  "gate": {
    "minimum_severity": "medium",
    "changes": ["introduced", "worsened", "preexisting"]
  },
  "rule_severities": {"K8S-SERVICE-EXPOSURE": "critical"},
  "exceptions": []
}
```

Valid severity levels are `low`, `medium`, `high`, and `critical`. Overrides must
name known deterministic rules. Findings preserve `original_severity`,
`effective_severity` and the effective `severity` used by the gate. A severity
override does not modify the observed declaration or erase its evidence.

## Scope an exception

Each exception must include exactly these fields:

```json
{
  "id": "EXAMPLE-CHANGE-123",
  "rule_id": "K8S-SERVICE-EXPOSURE",
  "target": "staging",
  "resource": "core/Service/web-staging/web",
  "owner": "Example platform reviewer",
  "reason": "Illustrative approved exposure requirement; replace with the actual reviewed rationale.",
  "expires": "2026-12-31"
}
```

Matching requires the exact selected target, rule and resource identity; wildcard
matching is unavailable. Resource identities have the form
`group/Kind/namespace/name`, using `core` for the core API group and `<cluster>`
for a cluster-scoped object. One exception can match multiple unresolved
findings for that same target, rule and resource; matched counts and fingerprints
are retained. The profile supports at most 100 exceptions with unique IDs.

Expiry is a canonical calendar date `YYYY-MM-DD`, valid through that date in UTC.
The report records the evaluation date. An expired or unmatched relevant exception
does not suppress findings and creates an advisory gap. Exceptions for an
unselected target do not apply. The engine checks structure and expiry, not
whether the named owner actually authorized the rationale.

Applied exceptions set `policy_suppressed: true` and record
`policy_exception_ids` on the retained finding. The report also lists each
applied exception's owner, reason, scope, expiry and matched findings. HTML and
Markdown explain the policy treatment so a pass with retained high findings is
reviewable.

## Independent validation still matters

Exceptions and severity overrides apply only to deterministic findings. They
cannot suppress schema failures, rendering errors, invalid or stale CI identity,
missing inputs or required coverage gaps. Required unavailable evidence keeps
the result `unknown`, even when the finding gate would otherwise pass. A pass
under the configured gate remains a scoped review result, not release approval.
