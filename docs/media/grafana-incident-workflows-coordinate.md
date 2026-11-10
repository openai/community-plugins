Profile status: `PARTIAL`; readiness `WARN`. `provider-read-authorization:grafana` is `WARN`, so anonymous-read permission remains uncertain. Other checks passed; this profile supports `SIMULATED` origin only.

Active packs: inventory `COMPLETE` — `grafana.apm`, `grafana.business-kpis`, `grafana.infrastructure`, `grafana.iot-edge`, `grafana.logs`.

### Incident brief — DRAFT

**Synthetic checkout regression** · Incident responders · Unsent  
As of `2026-10-31T19:45:26.901Z`

One authorized `SIMULATED` collection was performed for scope `apm`. Report `gfrpt_4f0d6e5dfea74175a303d5377a8d8db4725b715b7431a49037cf2bec054c0904` returned `COMPLETE`: all ten cards were retrieved as `COMPLETE`; collection times span `2026-10-31T19:45:23.792Z`–`19:45:26.901Z`. Current window: `19:44:30Z`–`19:44:50Z`; equal baseline: `19:44:10Z`–`19:44:30Z`. `COMPLETE` does not establish health or recovery.

**Observed:** Request rate rose 60→120 requests/s (`gfev_8f8ab32e07b2aaa8eed6bb92a07cc53321129819ea5003e798684d062e0ea6a7`, `gfev_b405ce438564f243a80fd30285066803fe517295a2e30746387fe0b7ddee991f`); error rate 3→6% (`gfev_6d8b8d0019e73f225ef410a7681ce93c3bbc4dd6de8c8464449b636cdf53e8db`, `gfev_7286b67d602209043aa65ac49be16207960937298ee1694c449fb8233d1d6d98`); p95 latency 625→1,250 ms (`gfev_3381fa3509cbeb68a56fc556ca4224ba12e638b074dc3ef6b109339abf9e7391`, `gfev_6e8a9c300bef1640d6a374ac3532c6790a4cbd5f6724241cb552494eff76976d`). Signed rules classify the error-rate and latency changes as `REGRESSION`.

1. **Open—load sensitivity:** concurrent doubling supports traffic-associated pressure. Counter-evidence: correlation only; no resource measurements. Next check: separately authorize the same-window `grafana.infrastructure` comparison.

2. **Open—persistent checkout/dependency fault:** HTTP 503 and >1,000 ms trace events appear in both baseline and current windows (`gfev_623fed65d9609f6c85b9bed4d6b5a5e5baa97a1dab2a6df732187214c07b4cdb`, `gfev_32c9d3d9817883463d6d7aee604311ab9591c42a41dc05a617eac31b34d021e3`, `gfev_c6f9991f4ecd3cd0b502379bb37853288bd54ebd82e99e0b919e589997c7ca41`, `gfev_4c70ce6696462c38867599e64952e4e586292f3ed7f32e5838158835acccb741`). Counter-evidence: no component attribution or 503 count. Next check: separately authorize bounded log-pattern comparison.

**Responder handoff — DRAFT, unsent:** Preserve the returned `EVIDENCE_GAP`/`BLOCKED` `DEVIATION`. No mitigation, authorization, execution, or effect is evidenced. Cause, severity, production impact, recovery, and current production health remain unestablished.

Resolved same-report link `gflnk_c2f2163e3ec2667ce26c30fb48e188d28bf9e14665db413fc1fd5a7aef8e36c3`: [Open Grafana evidence](http://127.0.0.1:63758/d/synthetic-apm?viewPanel=8).