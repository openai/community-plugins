# Community contribution preview

This package retains version `0.2.0-alpha.14` and all five workflows: Kubernetes
change review, GitOps diagnosis, correction preparation, recovery verification,
and explicit Software Factory demo operation. It is a development preview.

## Package identity

The November 12, 2026 contribution copy derives from the frozen R20 source
manifest `7c14b1416dc227ce3e28c6577b0fd84c6586278351ef69e480bd4fbafecb2323`.
It adds Apache-2.0 licensing and public repository metadata, corrects version
text, removes personal lab references, and includes the Registry1 redirect-policy
example. It also repairs the cold Linux validation image: bootstrap imports its
schema selector before use, and the final image includes public demo assets,
all runtime lockfiles and license files. Test fixtures select a native host Git
by absolute path and SHA-256 instead of an author-specific Homebrew version;
byte-identity checks and command deadlines remain in force. A separate
[portable GitOps observation path](portable-gitops.md) now lets the installed
diagnosis/recovery skills read actual scoped Argo CD, Kubernetes and Prometheus
APIs and compare their condition changes. It keeps aggregate unknown, missing
provenance and version/freshness gaps explicit. It does not change the original
Iron Bank adapters, schemas or qualification gates. The separate
[portable stack runbook](../demo/portable-gitops/README.md) includes explicit
GitLab, Kubernetes, Argo CD and Prometheus setup, real CI build/scan/publication,
and fault/recovery commands for one authorized disposable Linux VM. It uses a
fully trusted shell runner and does not claim production isolation.

Its archive and `SHA256SUMS` identities are different from R20. Compare
those hashes when reproducing a result; the shared preview version alone is not
an evidence binding.

The original R20 archive SHA-256 was
`5eb01b86ff57d4b4e88b3c8c11b51f6550fe58e2552211284bb3e8ace9174ced`.
That archive was staged without completed qualification or installation. This
changed copy does not inherit qualification from R20 or earlier alpha packages.

## Scope and access

Local review uses repository inputs, explicit profiles and separately provisioned
tools. GitLab, Kubernetes, Argo CD and Prometheus collection is read-only and
requires explicitly configured endpoints and scoped credentials. The optional
factory workflow also includes operator commands that create and remove lab
resources, publish images and deliver Git changes. Those commands retain their
explicit scope, approval, ownership and cleanup contracts; installing the plugin
does not authorize a deployment.

The source package contains no operator credentials, private lab configurations,
tool caches, downloaded container images, generated reports or customer data.
The customer-named profile is an unconfigured illustration, not an approved
customer policy. Supply actual environment details through private configuration.

## Qualification status

Package and static checks are separate from functional and live qualification.
Historical implementation receipts in the engineering ledger describe their
original versions and environments only. Before describing this preview as a
qualified release, complete its connected automated regression, a fresh installed
Codex check, and the applicable configured integration checks. Missing access
or intentionally unrun external checks remain explicit gaps.

The requested real code-to-GitLab-CI-to-cloud-to-Argo-CD recovery demonstration,
Prometheus evidence and narrated MP4 are not supplied by this package preparation.
Run the selected [portable stack](../demo/portable-gitops/README.md) or
[canonical Iron Bank demo](demo/ironbank-e2e.md), capture actual tool UIs,
review the recording for readability and credential exposure, and bind its
artifacts to the exact package before claiming that demonstration is complete.

## Distribution status

This is a source contribution prepared for the Community Plugins repository.
It has not been published, installed or approved for the official OpenAI Plugins
Directory. Publisher verification, directory policy URLs, country targeting and
any directory review attestations are not supplied or inferred here. Repository
review, applicable CLA verification and required checks remain merge gates.
