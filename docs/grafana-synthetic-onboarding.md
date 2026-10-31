# Synthetic Grafana incident onboarding

Use this repository companion to try successful Grafana evidence collection
without a Grafana account, token, Docker, plugin installation, or production
access. It requires Node.js 22.19+ and a reviewed checkout of this repository.

From the repository root:

```sh
node scripts/demo-grafana-synthetic.mjs
node scripts/demo-grafana-synthetic.mjs --json
```

The second command returns the full normalized evidence packet for inspection
or a separate incident-skill evaluation. It does not save the packet for you.
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

In a Codex task with the updated Grafana plugin, provide the returned evidence
packet and ask `coordinate-grafana-incident` for an unsent status/handoff draft,
or `review-grafana-incident` for a postmortem draft. Identify the packet as a
frozen synthetic source: it is not a fresh provider connection. Preserve its
scope, origin, timestamps, report/evidence ownership and limitations. Never
invent a deployment, a tested cause, an approved lesson, or a recovery result.

The companion performs no diagnosis or message delivery. Any separate model
evaluation must distinguish its skill behavior from this transport test.

## Cleanup and qualification boundary

The command closes its child server and HTTP fixture and removes its owned
temporary authority, state and artifacts on completion or a handled failure.
The JSON packet includes the cleanup result. Resolved links belong to the
ended fixture session; they are verification receipts, not a working Grafana
dashboard. An abrupt process kill or machine failure can interrupt cleanup.

This proves the copied package's MCP, worker, signed authority, transport and
normalization path against synthetic HTTP. It does **not** qualify a real
Grafana release, installed Codex client, authentication, production access,
causality, or recovery. The separate [test guide](../tests/grafana-observability/README.md)
describes those qualification paths. Source validation remains covered by the
[reproducible source guide](grafana-observability-source.md).
