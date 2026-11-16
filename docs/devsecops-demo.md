# Follow a Helm change from GitLab to Kubernetes

GitLab builds the service and publishes an image by digest. A deployment commit selects that digest. Argo CD reconciles the commit into Kubernetes, and Prometheus reports whether it can still scrape the service. The demo follows those identifiers across the tools so a viewer can check what actually reached the cluster.

[Watch the narrated metrics-port repair](media/devsecops/devsecops-metrics-fix.mp4) · [English captions](media/devsecops/devsecops-metrics-fix.en.vtt)

The video opens on the patch from `$prepare-gitops-correction`. It changes `metricsPort` from `9091` to `9090` in `values/component.yaml`. Helm rendered six resources before and after the patch. All six passed Kubeconform on both sides, and only the metrics NetworkPolicy’s ingress port changed.

| Handoff | What to look for |
| --- | --- |
| Source → GitLab CI | Open the pipeline at the source commit. Follow the build, SBOM and vulnerability scan, then the registry publication job. Read the scan decision alongside the finding count. |
| Registry → deployment Git | Match the published image digest to the digest in the Helm values commit. |
| Deployment Git → Argo CD | Match the deployment commit to the Application revision. Open the resource tree to see the workload and its NetworkPolicy. |
| Argo CD → Prometheus | Change the permitted metrics port from 9090 to 9091. Compare application health on 8080 with the Prometheus target and the connectivity probe. The two observations answer different questions. |
| Runtime evidence → Codex | Give the installed skill the fault revision and observations. Inspect the proposed values diff and the rendered NetworkPolicy change. |
| Validated patch → recovery | Deliver the accepted values, match the new Git revision in Argo CD, then compare fresh scrape and progress samples. Check that the denied source still cannot connect. |

## Reproduce the flow

Start with the [plugin quickstart](../plugins/devsecops/README.md) for a local Kubernetes or Helm review. For the private Linux demo, follow the [portable GitOps runbook](../plugins/devsecops/demo/portable-gitops/README.md). It covers service setup, the trusted disposable runner, build and scan inputs, Git delivery, observation, and cleanup. An exception for High findings requires approval for the specific findings and image. Critical or unexpected findings block publication.

The pipeline publishes an immutable image. The fault and correction change policy through Git and reuse that image. The [Iron Bank and Big Bang runbook](../plugins/devsecops/docs/demo/ironbank-e2e.md) describes the additional image trust and platform checks for that profile.

## Recorded milestones

The GitLab pipeline and initial Argo CD deployment were recorded in a private Azure lab. The pipeline built, scanned and published the image. The scan retained 28 High findings under a time-limited lab exception. Azure confirmed deletion of the resource group after the run.

The repair was recorded in a separate local k3s cluster running the mission-status application. Argo CD synced deployment commit `97ed569` with the wrong port, then commit `09eeec8` with the corrected policy. Prometheus recorded four failed scrapes followed by four successful scrapes. The event counter rose from 429 to 435 over the four successful samples. The same pod answered its health check with HTTP 200 throughout, and the unauthorized probe remained blocked.
