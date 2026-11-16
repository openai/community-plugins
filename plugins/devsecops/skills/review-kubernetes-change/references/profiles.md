# Profile selection

Profiles are versioned JSON data consumed by the review engine. The reusable core
defines checks and report semantics. A profile selects explicit paths, values,
target versions, namespaces, release names and policy expectations.

Use `profiles/generic.example.json` as a starting point only after adapting its paths to
the repository. The Lockheed example is illustrative configuration for a platform
team; it is not an approved Lockheed policy, deployment inventory, or compliance
baseline. Its name grants no access or authority.

The target path and values paths are repository-relative. Values paths refer to
files in the repository, not files relative to the chart. Keep each target's
Helm release, namespace, Kubernetes version, API-version capabilities and install
or upgrade mode explicit so both sides use the same rendering assumptions.

Do not put shell snippets, credentials, kubeconfigs, remote endpoints, or
executable paths in profiles. An external profile is explicit trusted
configuration. A profile inside the reviewed repository is read from its regular
tracked file at the immutable baseline commit; edits, deletion or symlink
replacement in the candidate cannot change this review's policy. Record its
origin, identity and digest. Treat a policy change as a separate reviewed change,
then use an approved baseline or explicitly selected external version.

The default finding gate is high-or-higher introduced/worsened findings. Trusted
profiles may choose severity thresholds and change classes, override known rule
severities, and define exact target/rule/resource exceptions with an owner,
reason and UTC expiry date. Exceptions never remove findings or suppress schema
failures and required gaps. Explain original versus effective severity and every
applied exception; a passing gate does not erase the retained risk. See
[policy configuration](../../../docs/policy.md).

If policy or schema coverage is absent, preserve that gap. Never claim that the
generic or example profile establishes CIS, STIG, NIST, FedRAMP, or ATO compliance.
