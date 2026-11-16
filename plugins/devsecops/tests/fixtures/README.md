# Synthetic review fixtures

All fixtures are synthetic and intended for local rendering and review. They
are not customer configuration or deployable recommendations. The image registry
uses the reserved `.invalid` domain and cannot supply an image.

- `helm-web/`: a dependency-free Helm chart, with baseline staging values and a
  deliberate risky alternative. Covers workloads, services, RBAC, service
  accounts, network policy, PVC deletion, and optional Secret redaction.
- `manifests-valid/`: a small schema-valid ConfigMap target.
- `invalid-manifests/`: a Deployment with an invalid replica type, so schema
  validation must not report a pass.

End-to-end tests create their own temporary Git repositories and exact revisions.
They never connect to Kubernetes. Real Helm, Kubeconform, and local schemas are
required to prove those integration paths.
