# Demo credentials

The factory operator uses a separate private directory outside the source tree.
After the owner authorizes creation of an isolated lab, initialize its bootstrap
passwords once:

```sh
uv run --frozen factory-demo init-credentials \
  --directory /absolute/private/operator-directory \
  --environment-id devsecops-lab-001
```

The command returns a retrieval path and permissions, never password values.
Directories must be owned by the operator with mode `0700`; the new
`credentials.json` has mode `0600`. Existing files are never overwritten, and
symlinks are rejected. Store this directory outside repositories, release ZIPs,
evidence bundles and shared drives. The file contains retrievable plaintext
bootstrap passwords; access it through the local filesystem or move it to the
operator's approved secret store.

Creation does **not** provision accounts or establish access. The initial record
is explicitly `not_provisioned`. A later operator phase must configure GitLab and
the lab registry to accept their respective bootstrap credentials. Read-only
collectors need separately issued, scoped API tokens; these passwords are not
API tokens. Review, diagnosis and recovery skills must not invoke credential
initialization or gain operator privileges.

Registry1 and hosted Party Bus use separately issued Platform One identities.
Generating local passwords cannot grant that access. Platform One's
[account instructions](https://p1docs.dso.mil/p1/get-started/create-a-p1-account)
require an approved email address, an onboarding supervisor, the required
authentication, and truthful affiliation. Its
[application-access workflow](https://p1docs.dso.mil/p1/get-application-access/request-application-access)
requires separate service approval. Do not create a duplicate account or invent
an affiliation to satisfy the demo. An unavailable external grant remains an
explicit preflight gap while local implementation continues.
