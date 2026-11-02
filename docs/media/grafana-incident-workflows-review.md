### Incident review — DRAFT

**Synthetic checkout regression** · Incident responders · Historical `SIMULATED` review

Report `gfrpt_4f0d6e5dfea74175a303d5377a8d8db4725b715b7431a49037cf2bec054c0904` returned `COMPLETE`; no new collection was performed. The recorded profile remained `PARTIAL`/`WARN`, with anonymous-read permission uncertain. Evidence was collected `2026-10-31T19:45:23.792Z`–`19:45:26.901Z`; this does not establish current or production health.

**Evidenced timeline**

- Baseline `19:44:10Z`–`19:44:30Z`: 60 requests/s, 3% errors, 625 ms p95; HTTP 503 and >1,000 ms trace events were present (`gfev_8f8ab32e07b2aaa8eed6bb92a07cc53321129819ea5003e798684d062e0ea6a7`, `gfev_6d8b8d0019e73f225ef410a7681ce93c3bbc4dd6de8c8464449b636cdf53e8db`, `gfev_3381fa3509cbeb68a56fc556ca4224ba12e638b074dc3ef6b109339abf9e7391`, `gfev_623fed65d9609f6c85b9bed4d6b5a5e5baa97a1dab2a6df732187214c07b4cdb`, `gfev_c6f9991f4ecd3cd0b502379bb37853288bd54ebd82e99e0b919e589997c7ca41`).
- Comparison `19:44:30Z`–`19:44:50Z`: 120 requests/s, 6% errors, 1,250 ms p95; both event types remained present (`gfev_b405ce438564f243a80fd30285066803fe517295a2e30746387fe0b7ddee991f`, `gfev_7286b67d602209043aa65ac49be16207960937298ee1694c449fb8233d1d6d98`, `gfev_6e8a9c300bef1640d6a374ac3532c6790a4cbd5f6724241cb552494eff76976d`, `gfev_32c9d3d9817883463d6d7aee604311ab9591c42a41dc05a617eac31b34d021e3`, `gfev_4c70ce6696462c38867599e64952e4e586292f3ed7f32e5838158835acccb741`).

**Assessment:** The report’s `SIGNED_RULE` classifications support error-rate and latency `REGRESSION`. Load sensitivity ranks first but remains a hypothesis; correlation and absent resource evidence prevent causal confirmation. A persistent path/dependency fault also remains open because the 503 and slow trace predate the comparison window.

**Actions and limits:** Proposals: separately authorize same-window infrastructure and log comparisons. Approval, execution, mitigation, effect, deployment, and human actions: not established. Preserve the `EVIDENCE_GAP`/`BLOCKED` `DEVIATION`.

**Proposed recovery criteria:** define acceptable error-rate and p95 thresholds plus duration, then obtain a separately authorized fresh same-scope measurement. No criterion is presently evaluated or met.

Resolved link `gflnk_c2f2163e3ec2667ce26c30fb48e188d28bf9e14665db413fc1fd5a7aef8e36c3`: [Open Grafana evidence](http://127.0.0.1:63758/d/synthetic-apm?viewPanel=8).