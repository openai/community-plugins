# Local Review Workbench verification

Run the complete automated package suite from the repository root:

```sh
npm run test:local-review-workbench
```

The suite uses Node.js built-ins and needs no dependency installation, network,
credentials, or model access. It checks the marketplace/manifest contract,
read-only declared capabilities, bundled skill discovery and default prompts,
local references in an isolated copied package, absent runtime integrations,
and a limited set of obvious private-content/credential patterns. It also
executes the synthetic API fixture to confirm that its unchanged consumer
succeeds before the change and fails after the response-field rename.

These checks establish packaging and fixture correctness. They do not prove
that a model follows the instructions, that a review is accurate, or that the
plugin installs successfully in a particular Codex client. The content scan is
also not an exhaustive secret scanner.

## Installed-client and behavior evaluation

Use the package's [evaluation guide](../../plugins/local-review-workbench/evaluations/README.md)
in a newly started Codex task after installing the candidate from its exact
branch or commit. Record the client version, plugin revision, available agent
capabilities, scope, actual findings, test execution, latency, and approximate
usage if available. Keep evaluation records local and sanitized.

Before marking the preview ready, record human-observed results for:

- Fresh installation from only the catalog and plugin sparse paths; the
  documented first prompt finds the skill and its bundled references.
- Synthetic compatibility break: explain the requirement, identify the old
  consumer contract, and report the demonstrated failure once.
- False positive: reject a candidate when the inspected caller or requirement
  disproves it, including a duplicate of an existing hosted-review comment.
- Missing history or unavailable subagents: declare the missing context and
  perform bounded sequential passes without inventing independent reviewers.
- A changed base, head, or working tree during review: mark affected findings
  stale and rescope before presenting them as current.
- Review-only behavior: no source edits, git mutations, hosted comments, or
  feedback messages; recommendations and feedback remain drafts.
- Requirements and readability: avoid unrelated cleanup and distinguish
  optional naming/simplification suggestions from concrete defects.

Run representative consenting dogfood reviews in both a small repository and
a larger repository with scoped instructions. Have a human label accepted
findings, false positives, duplicates, missed issues and optional suggestions.
Compare local and hosted reviews on the same revision without assuming either
is universally better. Record unavailable or unrun evaluations explicitly;
they are not passing checks.

The plugin workflow runs one master job with the same root command on Node
22.19.0, 24 and 26 sequentially. Marketplace CI discovers this same master
command through the plugin's catalog name. Hosted CI results and installed-client
behavior results must be reported separately from a local package-test run.
