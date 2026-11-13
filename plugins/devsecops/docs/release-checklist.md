# Release checklist

Use this checklist for a specific source revision and package digest. It records
local v0.1 qualification and the separately pending customer-pilot gates. Record
receipts in the [validation record](test-validation.md) and keep pending items
visible until they have direct evidence.

## Source and scope

- [x] Freeze the final source revision and record its identifier.
- [x] Confirm README, native skill, CLI help, report schemas and support matrix
  describe the same implemented scope.
- [x] Review the generic and Lockheed examples; retain the explicit unconfigured
  customer-example state and exclude actual customer profiles/data.
- [x] Verify that all findings and exception/gate behavior match documented
  semantics and fixture expectations.
- [x] Check that the package has no required preview-only service or unverified
  product entitlement.

## Runtime and evidence

- [x] Run the full test suite against the final source with real pinned Helm,
  Kubeconform and local schemas. Record command, platform, versions, counts and
  captured output; skipped integration tests do not satisfy this item.
- [x] Exercise baseline pass, risky candidate fail, schema-invalid fail and
  missing-required-evidence unknown through the actual CLI.
- [x] Verify trusted profile selection, configured gates and scoped/expiring
  exceptions; candidate-controlled policy must not grant itself approval.
- [x] Verify malformed inputs, tool failures and early failures produce truthful
  status and any required failure manifests.
- [x] Validate emitted report JSON against the published report schema and
  independently recompute final artifact hashes from the terminal manifest.
- [x] Verify no source checkout/index mutation and no Secret/token/artifact-body
  disclosure in stdout, stderr or saved outputs.
- [x] Exercise GitLab exact-MR/head binding, stale/racing/missing/failed CI,
  pagination and artifact cases. State separately whether evidence comes from
  controlled fixtures or an approved live instance.
- [x] Record the supported-host test matrix. Provisioned binaries for another
  architecture are not a successful run on that architecture.

## Report and native-host experience

- [x] Inspect an actual engine HTML report at desktop and narrow-screen widths;
  check headings, wrapping, cards, evidence disclosure, contrast and long paths.
- [x] Inspect dark appearance and keyboard evidence disclosure on the recorded
  desktop/mobile rendering surface.
- [ ] Inspect light appearance and print/export behavior where the verified
  surface supports them; retain these as documented limits until exercised.
- [x] Record screenshots or equivalent direct visual evidence from an allowed
  rendering surface. Static CSS or HTML parsing alone is not visual inspection.
- [x] Confirm the report preserves unknown/incomplete states and does not hide
  independent confirmed failures under missing evidence.
- [x] Install the final package through the supported local/private host flow;
  record marketplace, package/version, component diagnostics and installed path.
- [x] Discover and invoke `$review-kubernetes-change` in the actual installed
  host against a synthetic repository. Confirm the installed runtime produces
  the expected reports and preserves the checkout.

If browser policy rejects a rendering route, do not treat a workaround as
approval. Record the refusal and use an explicitly permitted first-party preview
or obtain the missing authorization. Keep visual qualification pending until an
actual result has been inspected.

## Packaging and distribution

- [x] Rebuild the ZIP and source-marketplace snapshot after the last source edit.
- [x] Validate both manifests, skill frontmatter, agent metadata, referenced
  assets, documentation links, lockfiles and archive membership.
- [x] Exclude environments, caches, generated reports, credentials, symlinks,
  tool binaries and non-example customer profiles.
- [x] Verify SHA-256 checksums and archive integrity. Identify checksums as
  integrity evidence; do not imply a publisher signature.
- [x] Preserve the previous package, installation identifier and rollback steps.
- [ ] Name a support owner, supported version window, incident-reporting route,
  and process for triaging false positives/negatives before a customer pilot.
- [x] Keep local install, workspace sharing, public publication and customer
  deployment approval as separate recorded actions.

## Release decision

The release owner records `ready`, `ready with documented limits`, or `not ready`
alongside the final package digest and unresolved items. Do not mark the release
complete because source tests or a package build alone succeeded. Customer
distribution also needs the owner's approval for the actual release/destination.

## Local v0.1 disposition

**Ready with documented limits for local evaluation.** The accompanying release
receipt records the source revision, final ZIP checksum and installed-content
verification. The proposed second-human installation replay has not occurred;
an independent agent replay is separate evidence. Customer-pilot acceptance,
owner/support/licensing/publisher decisions and any authenticated customer GitLab
check remain open. Light/print appearance and unlisted host architectures remain
unqualified. No public publication or customer deployment approval is implied.
