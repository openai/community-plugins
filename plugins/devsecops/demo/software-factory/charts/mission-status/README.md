# Mission workload chart

This chart deploys the synthetic mission application built from the qualified
Iron Bank Python base. It requires the actual CI image repository and digest,
environment identity, architecture, and the observed namespace and pod labels
of the authorized health and metrics callers. Empty defaults intentionally fail
Helm's values-schema validation. The chart does not download dependencies,
create credentials, provision a namespace, or install a monitoring operator.

One replica exposes `/health` on 8080 and `/metrics` on 9090. The application
produces mission events independently of either HTTP listener. It runs as
UID/GID 10001 with a read-only root filesystem, no service account token, no
capabilities, and bounded CPU/memory. Both listeners remain fixed across D2.

Network policies select this release's mission Pods only. Ingress and egress
start denied; separate policies grant health and metrics access to the **same
peer's namespace AND pod labels**, rather than either selector independently.
No DNS or general outbound permission is needed by this application itself.
The qualified profile must account for any injected mesh containers separately.
The chart alone establishes neither CNI enforcement nor application recovery.

The harness injects D2 through a Git change to `networkPolicy.metricsPort`.
A correction contract can permit only the metrics policy's
`/spec/ingress/0/ports/0/port` field. Deployment, image, default-deny policy,
health access, peer selectors and monitoring discovery remain held constant.
The resulting repair must come from the actual native skill and pass the
independent patch validator and runtime assertions.

Prometheus discovery and the unchanged reference NGINX workload are separate
factory assets. The exact monitoring configuration, CNI/mesh behavior, image
trust, runtime image mapping and negative probe controls still require live
qualification. Local Helm/schema tests must not be described as that proof.
