# Synthetic Grafana incident onboarding

Use this repository companion to try successful Grafana evidence collection
without a Grafana account, token, Docker, plugin installation, or production
access. It requires Node.js 22.19+ and a reviewed checkout of this repository.

Watch the [recorded incident workflow](grafana-incident-video.md) to see both new
skills use the synthetic provider in a real Codex session, including the
one-time collection approval and evidence reuse during review.

**Choose a starting point:** [Run the synthetic demo](#quick-start) · [Install the skills](#use-the-result-with-incident-workflows) · [Connect a real Grafana instance](../plugins/grafana-observability/README.md#connect-a-real-grafana-instance).

## Preview a draft PR

The `main` marketplace contains merged releases. To try the incident workflows
while [PR #48](https://github.com/openai/community-plugins/pull/48) is a draft,
use its reviewed branch in a separate checkout:

```sh
git clone --branch codex/grafana-incident-workflows https://github.com/openai/community-plugins.git community-plugins-grafana-preview
cd community-plugins-grafana-preview
```

If you already have a checkout of this PR, use that directory. Review the PR's
current diff before installing. The steps below run from the repository root;
they require the full checkout, not a sparse plugin-only install.

## Quick start

From the repository root:

```sh
npm run demo:grafana:synthetic
node scripts/demo-grafana-synthetic.mjs --json > grafana-incident-evidence.json
```

The first command prints the collection summary. The second runs a new
collection and saves its full normalized evidence packet using shell redirection.
No dependency installation or build step is needed. Without redirection,
`--json` only prints the packet.
The command accepts only `--json` and the fixed `--scenario` values below; it
cannot accept a provider URL, token, query, output directory, or live profile.

## What actually runs

The companion copies the installed-shaped plugin into a private temporary
directory, starts its real MCP stdio server and signed provider worker, and
collects through a strict loopback synthetic Grafana HTTP fixture. It uses the
shipped authority CLI to validate and activate an ephemeral **SIMULATED-only**
test authority bound to the exact worker bytes. The private signing key stays
in memory. Provider credentials and existing user authority are not inherited.

The normal installed `fixture` profile still has no active Grafana packs. This
companion's disposable authority does not enroll anything into that profile,
the user's Codex configuration, or a real monitoring system.

The sequence uses the actual five MCP tools: profile status, active pack list,
pack collection, evidence detail, and owned source-link resolution. It does
not return a canned report or bypass the provider normalizers. Anonymous test
authorization remains `PARTIAL`/`WARN`; successful collection does not erase
that warning.

The default APM case collects five required operations in each of two
equal-duration, non-overlapping windows. It contains synthetic request rate,
HTTP 503 evidence, error rate, p95 latency and a slow trace. A signed rule may
identify a regression. **Those facts do not establish its cause or recovery.**

## Try incomplete and conflicting evidence

```sh
node scripts/demo-grafana-synthetic.mjs --scenario partial --json
node scripts/demo-grafana-synthetic.mjs --scenario empty --json
node scripts/demo-grafana-synthetic.mjs --scenario stale --json
node scripts/demo-grafana-synthetic.mjs --scenario contradictory --json
```

| Scenario | Actual fixture behavior | Expected report |
| --- | --- | --- |
| `complete` | All APM operations return bounded evidence in both windows | `COMPLETE` |
| `partial` | Required slow-trace reads fail | `PARTIAL` |
| `empty` | Every APM operation returns no observations | `EMPTY`, not zero errors |
| `stale` | APM evidence uses old evaluation windows | `STALE` |
| `contradictory` | Two infrastructure operations claim different values for the same normalized scalar | `CONTRADICTORY` |

The contradiction case uses the infrastructure pack; the other cases use APM.
All remain visibly `SIMULATED`. A successful command exit means the requested
scenario ran, including its expected incomplete or conflicting result. It
does not mean the synthetic service is healthy.

## Use the result with incident workflows

To load the two skills from this reviewed checkout, install its local marketplace:

```sh
codex plugin marketplace add . && \
codex plugin add grafana-observability@community-plugins
```

If `community-plugins` is already registered from another source, the first
command refuses to replace it and the chained install stops. Inspect
`codex plugin marketplace list --json`. To deliberately switch to this checkout,
remove the existing registration with `codex plugin marketplace remove community-plugins`,
then rerun the commands above. Keep the previous source if you want to restore it
after the preview; this changes the marketplace source for its other plugins too.

Start a new Codex task in this checkout, then use these prompts in order:

```text
Use $setup-grafana-observability to check the fixture profile and available packs.

Use $coordinate-grafana-incident to read ./grafana-incident-evidence.json and
draft an incident handoff. Treat it as a frozen SIMULATED source, preserve exact
evidence references and gaps, and use only this supplied record. Do not collect
new evidence or infer current health, a confirmed cause, or verified recovery.

Use $review-grafana-incident with the same frozen packet and handoff to draft
an incident review. Separate observations, hypotheses, and proposed follow-up
checks; keep cause and recovery unverified.
```

The setup check should report that the installed fixture has no active packs.
That does not prevent drafting from the supplied packet. These prompts use the
exported record after its provider has closed; they do not reproduce the video's
live tool calls. For connected collection, follow [Grafana setup](../plugins/grafana-observability/README.md#connect-a-real-grafana-instance).

The companion performs no diagnosis or message delivery. Any separate model
evaluation must distinguish its skill behavior from this transport test.

## Cleanup and qualification boundary

The command closes its child server and HTTP fixture and removes its owned
temporary authority, state and artifacts on completion or a handled failure.
The JSON packet includes the cleanup result. Resolved links belong to the
ended fixture session; they are verification receipts, not a working Grafana
dashboard. An abrupt process kill or machine failure can interrupt cleanup.
The exported `grafana-incident-evidence.json` is your local copy; delete it when
you finish reviewing it.

This proves the copied package's MCP, worker, signed authority, transport and
normalization path against synthetic HTTP. It does **not** qualify a real
Grafana release, installed Codex client, authentication, production access,
causality, or recovery. The separate [test guide](../tests/grafana-observability/README.md)
describes those qualification paths. Source validation remains covered by the
[reproducible source guide](grafana-observability-source.md).
