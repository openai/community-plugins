# DevSecOps community test suite

The canonical root command is:

```sh
npm run test:devsecops
```

The marketplace dispatcher calls that same command. It runs static package/privacy checks, prepares the locked test environment, executes **every packaged Python test** (6,498 collected cases: the 6,292-case baseline plus native-Git, portable-observation, portable-demo and packaging checks), then builds the ZIP twice and exercises the extracted package with real Helm/Kubeconform. No `-k`, marker selection or replacement test implementation narrows the master suite. A smaller static command is available as `npm run validate:devsecops`; its result is not a master pass.

## Cold start and network behavior

Use Node 22.19+, Python 3.11+, native Git and OpenSSL supporting `req -addext` and `verify -x509_strict` on Linux/macOS amd64 or arm64. Set `DEVSECOPS_TEST_OPENSSL` to select a compatible executable; unsupported system LibreSSL fails explicitly instead of silently skipping strict TLS tests. Install uv 0.13.7 explicitly before running the master; a different reported version stops the run before dependency acquisition. The dedicated workflow uses Python 3.14 and the upstream repository's pinned action revisions.

The first master invocation is an explicit test-setup operation: it resolves the frozen `uv.lock` into an external environment and invokes the package's existing checksum-pinned bootstrap helpers. This fetches Helm, Kubeconform, both packaged Kubernetes schema sets, Kustomize, Cosign, Syft, Grype and the exact public mission dependency wheel. Destinations are the pinned official release URLs in the package locks, PyPI/Python package storage, Helm's download host and the pinned Kubernetes schema repository. It does not fetch a vulnerability database, register an account, invoke a model, start a Docker daemon/VM/cluster, or access GitLab/cloud credentials.

All preparation and receipts go beneath `~/.cache/devsecops`; no dependency environment, bytecode or test output is written into the plugin. `DEVSECOPS_TEST_CACHE` may select a different child of that private cache root. The mission wheel also occupies the fixed cache path used by existing tests. The bootstrap Python and uv executable can be explicitly selected with `DEVSECOPS_TEST_BOOTSTRAP_PYTHON` and `DEVSECOPS_TEST_UV`. The two native-Git protocol suites accept test-only `DEVSECOPS_TEST_GIT` and `DEVSECOPS_TEST_GIT_SHA256` together; otherwise they discover native host Git once and retain its hash. Scripts/shims are rejected, and every invocation checks the selected executable's identity.

After an online setup, an offline run is explicit:

```sh
DEVSECOPS_TEST_OFFLINE=1 npm run test:devsecops
```

Offline mode refuses missing/changed prerequisites. It never silently drops real Helm, Kubeconform, Kustomize, Cosign or scanner cases. Cache receipts and hash equality are integrity checks, not independent publisher authentication. Component tests use synthetic data and controlled endpoints; they do not claim a live service was qualified.

## Coverage and artifacts

- `unit/` verifies installable manifests, catalog/master dispatch, all five skill entrypoints and their bundled runtime references.
- `integration/` builds reproducible archives and runs actual safe/risky/missing-tool Helm reviews from a new extraction and unrelated working directory. A Python audit hook rejects access to the original checkout and network calls in that Python process. This is an application-level guard, not an OS sandbox. The test verifies decisions, manifests, expected findings/gaps and both plaintext/base64 secret redaction.
- `security/` checks the public package for accidental private state, personal paths, recordings, coordination links, keys and symlinks. Deliberately injected artifacts demonstrate rejection. These checks supplement the deeper parser, path, redaction, transport, archive, process and authorization tests in the complete Python suite.
- `support/run_python.py` enforces the 6,498-case baseline and zero deselection; it writes `collection.json` before execution, streams each result and bounded failure/skip reason to `outcomes.jsonl`, and emits JUnit plus every skipped case and reason in `python-suite.json`. The complete Python phase has a 75-minute ceiling; individual test deadlines are unchanged. Missing required real-tool coverage, an unclassified skip, or a wall/monotonic clock discrepancy above five seconds fails the master. Its explicit external/platform reason list must be reviewed when coverage changes. An import failure in the canonical implementation is not an allowed external gap. `master.json` records step outcomes, interruption signals and whether package files remained unchanged. Interrupted/timed-out child groups receive termination and bounded forced cleanup, including descendants that outlive their leader.

The existing suite includes opt-in native-client configuration, macOS network-denial, case-insensitive-filesystem, retained genuine-database, and staged builder-archive cases. Their actual skips remain explicit coverage gaps. The master does not enable `DEVSECOPS_REAL_CODEX_CONFIG`, `DEVSECOPS_TEST_NETWORK_SANDBOX` or supply a genuine database on a user's behalf. In particular, the macOS sandbox opt-in is rejected on Linux. A local master may finish with documented external/platform skips; that does not establish those integrations or a complete live qualification.

The single DevSecOps workflow runs the full master on Ubuntu/Node 22.19, audits locked runtime dependencies, then repeats the Node-facing package/integration/privacy contracts sequentially on Node 24 and 26. The language-independent Python suite runs once. There is no platform matrix or separate native job. The repository-wide marketplace job also invokes the same master on its configured macOS host; its shared deadline is 150 minutes, including the existing catalog suites, cold setup and the complete DevSecOps phase. The dedicated job retains its 120-minute deadline. A measured Linux run reached only 81% of the suite at the former 60-minute ceiling; this changes orchestration budgets, not test assertions or selection. Neither budget is evidence of a completed run. On macOS the shared job selects an installed OpenSSL3 through Homebrew's reported prefix, installing it explicitly if needed.

To rerun the companion contracts against an already prepared environment:

```sh
DEVSECOPS_TEST_CONTEXT="$HOME/.cache/devsecops/community-tests/latest-context.json" npm run test:devsecops:contracts
```

These commands create evidence when actually executed. Their presence in the repository is not evidence of a passing run. Real Registry1/Iron Bank trust, build/sign/publication, hosted GitLab CI, Kubernetes/Argo/Prometheus behavior, native Codex contribution, recovery/replay, cleanup and the narrated recording require their own live receipts. Human/customer acceptance and the community repository's CLA/code-owner checks remain separate.
