# Grafana plugin tests

Run from the repository root with Node.js 22.19+ and Git. Default tests need no
`npm install`. Integration tests use disposable local services and credentials,
never a production Grafana instance.

| Command | Coverage / requirements |
| --- | --- |
| `npm run validate` | Marketplace, plugin layout, schemas, native asset, and entrypoint integrity. |
| `npm run test:grafana-observability` | Canonical master (alias: `test:grafana`): copied-package MCP protocol, five-pack synthetic collection, synthetic onboarding, incident evaluator checks, error/scope/ownership checks, media, and provenance. External integrations and model evaluations require explicit opt-in. |
| `npm run demo:grafana:synthetic` | Credential-free repository onboarding using actual packaged MCP collection against bounded synthetic HTTP; see the [onboarding guide](../../docs/grafana-synthetic-onboarding.md). |
| `npm run test:grafana:behavior -- --prepare` | Prepare actual packaged-tool evidence packets and exact candidate instructions without a model turn. See the [evaluation guide](../../docs/grafana-incident-evaluations.md) for opt-in runs and independent semantic review. |
| `npm run test:grafana:live` | All five packs and 17 operations against real Grafana 12.1.0, Prometheus, Loki, Tempo, and SELECT-only PostgreSQL. Requires local Docker/Compose. |
| `npm run test:grafana:matrix` | The same real-provider checks across the four pinned Grafana releases. Requires local Docker/Compose. |
| `npm run test:grafana:auth` | Real read-token admission and revocation. Requires local Docker/Compose; CLI terminal input/storage use explicit test seams. |
| `npm run test:grafana:installed` | Official, SHA-256-pinned Codex 0.144.3 installs the exact package, discovers all eight skills, rejects inactive collection, and runs signed synthetic APM through the installed MCP in both launcher modes. Verifies the five-setting environment allowlist. Requires local Docker and the pinned client/image downloads; execution is network-disabled with no host configuration mounted. |
| `npm run test:grafana:keychain` | Creates, reads, rotates, and deletes one disposable OS credential. macOS arm64 only. |
| `npm run test:grafana:handoff` | Real PTY → Keychain → shared-profile broker → packaged MCP. Requires macOS arm64, Python 3, local Docker/Compose, and an **absent** shared-profile store. Creates and deletes one real disposable profile/Keychain item, then removes only its verified empty metadata. Refuses existing user state. |
| `npm run verify:grafana:source` | Dependency-free source/archive and generated-file integrity verification. |
| `npm run verify:grafana:source -- --rebuild --test` | Locked source build, exact output comparison, typecheck, and source regressions. macOS arm64, npm 10+, Git, and OpenSSL required; add `--offline` when dependencies are cached. |

## Recorded qualification

Incident-workflow qualification on 2026-10-30 (macOS arm64, Node 26.0.0):

- The complete marketplace master suite and package validators passed after the
  incident-workflow changes; its Grafana checks include the retained evaluations.
- The final source archive passed 183 tests with no failures or skips and
  reproduced all 64 runtime files exactly. Runtime bytes remain unchanged.
- The final installed package passed both official Codex 0.144.3 launcher modes
  in isolated Linux arm64 containers: eight discovered skills, five tools,
  inactive-pack denial, and ten synthetic APM reads with owned evidence/links
  and successful cleanup. These installation checks submit zero model turns.
- [Retained incident evaluations](review-evidence/incidents/2026-10-30/manifest.json)
  contain nine independently reviewed passing scenario outputs from Codex CLI
  0.144.3, explicitly requesting `gpt-5.6-sol`; JSON traces did not report a
  resolved model identifier. One additional current-candidate response failed
  exact inline citation fidelity and is retained with its failing review; a
  separate repeat passed. Five format-calibration turns and all nine turns from
  before the retry-guidance correction are also retained, excluded from
  acceptance. Total: 24 incident-scenario turns, each with zero tool attempts.
- These model results qualify reasoning over frozen synthetic evidence. They
  do not establish installed-plugin model activation, autonomous MCP collection,
  production monitoring, remediation, publication, recovery or a reliability rate.
  Real-provider, authentication and native-keychain checks below are the earlier
  runtime baseline, not newly repeated production qualification.

Local qualification on 2026-08-20:

- **Final default suite:** 26 passed, zero failed, six explicit opt-in skips on
  each of Node 22.19.0, 24.19.0, and 26.0.0 / macOS arm64.
- **Source:** 183 passed, zero failed/skipped; all 64 generated files reproduced
  byte-for-byte from a fresh extraction on macOS arm64 / Node 26.0.0.
- **Real providers:** Grafana 10.4.19, 11.6.8, 12.1.0, and 13.0.6 each completed
  all five packs and 17 operations. Fixture evidence remains `SIMULATED`.
- **Authentication:** two tests passed. Grafana 12.1.0's legacy write-capable
  Viewer was rejected; 12.4.0's read-only Viewer was accepted. Invalid, Admin,
  and revoked tokens were rejected, including fresh collection after revocation.
- **Native Keychain:** both tests passed without skips on Node 22.19.0 and 26.0.0.
- **Actual Codex installation:** one test covers two required launcher phases
  (bundled plugin and explicit shared configuration). Three consecutive fresh
  runs passed with official Codex 0.144.3 / Linux arm64. Each waits for the exact
  task's MCP-ready notification before requesting inventory; no model turn is
  submitted, and no host client configuration is mounted.
- **Full native handoff:** two tests passed without skips on macOS arm64 / Node
  26.0.0. The real hidden-prompt CLI connected and inspected one disposable
  profile; the real Keychain broker supplied its credential to a fresh packaged
  MCP worker for four authenticated operations. Official deletion removed the
  profile and Keychain item; guarded cleanup restored the initially absent store.

The installed-client check uses Codex's actual installer and app-server in a
fresh Linux container, without a model turn. It verifies exact installed bytes,
eight discovered skills, cache-relative launch paths, the five tools, inactive
fixture denial, signed synthetic APM collection, and environment filtering.
The Desktop GUI remains outside this check. The authenticated CLI fixture
uses scripted input and an in-memory store; its MCP worker uses a separately
enrolled test credential reference. That fixture alone does not prove the
combined real-terminal/Keychain/shared-profile handoff; the separate native
handoff test covers that path without those seams. It does not automate the
Desktop GUI or make a production Grafana connection.

The installed-client check qualifies ordered startup and synthetic collection.
Simultaneous startup against shared artifact/state directories remains outside
this check. Storage rejects unmarked
roots and uncertain/live writer locks; those checks are not bypassed or retried
to make the tests pass.

An x64-emulated Linux run encountered a cold-worker admission failure; it is not
a native Linux/x64 qualification. The checked-in GitHub Actions workflow runs
the default package suite on Node 22.19, 24, and 26, then exercises the real
Grafana stack, token admission/revocation, and actual Codex installation on a
native Linux runner. Native Keychain and full PTY-to-Keychain handoff checks
remain macOS-arm64 release checks. See the repository's Actions page for the
latest hosted result; the recorded qualification above is the reviewed local
baseline, not a claim about future runs.

See the [live topology](support/live-stack/README.md),
[authentication fixtures](support/authenticated-stack/README.md), and
[source/build notes](../../docs/grafana-observability-source.md) for setup and
test boundaries. Skipped checks are not passes. Temporary stacks are removed
after execution; downloaded base images remain cached.
