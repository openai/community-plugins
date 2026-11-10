# Grafana demo media

## New incident workflow recording

The [incident workflow guide](../grafana-incident-video.md) links a real Codex run
of `coordinate-grafana-incident` and `review-grafana-incident` against the signed
synthetic provider. It includes an edited walkthrough, the full recording, exact
drafts, normalized MCP receipts, and an editing/provenance manifest. Its custom
recording console is visibly labeled; it is not a shipped product interface.

## Earlier production-monitoring illustration

The earlier illustration remains available as an [8×, 22.6-second clip](grafana-production-monitoring-demo-8x.mp4) and a [180.767-second full walkthrough](grafana-production-monitoring-demo.mp4). Both are silent H.264/yuv420p videos at 1680 × 956, 30 fps, with fast-start enabled and source metadata removed. The matching [poster](grafana-production-monitoring-demo.png) is the redacted full-video frame at 2:07.5. The plugin README now features the incident workflow recording above.

This illustrates a local `checkout-evidence` workflow, **not the packaged plugin's end-to-end test results**. The metrics, investigation, and human approval flow are retained; only identity/UI display text is replaced. An on-screen caption makes that distinction explicit.

## Redactions and review

Opaque masks and generic labels cover the following regions. Times refer to the full-speed video; moving labels follow panel animations.

| Full-speed interval | Redacted display text |
| --- | --- |
| Throughout | Customer/project dashboard branding and browser profile/chrome labels |
| Before 0:54 | Workspace project selector |
| 0:04–0:12 and 0:21–0:46 | Terminal account, host, project, and container labels, including clipped edges |
| While visible | Internal model selector; the actual approval controls remain unobscured |

The review combines local OCR, quarter-second full-video samples, every frame of the 8× video, full-rate terminal/animation checks, and visual inspection. Both encoded outputs were fully decoded. OCR and sampling cannot prove the absence of every disclosure; this is a content review, not legal or brand clearance.

[The manifest](grafana-demo.json) records exact durations, byte counts, checksums, and review coverage. The unredacted original recording is preserved outside this repository and is not a distribution asset.

## Regenerate the fast clip

Use the **redacted full walkthrough**, never the unredacted source:

```sh
ffmpeg -nostdin -y -i docs/media/grafana-production-monitoring-demo.mp4 \
  -map 0:v:0 -vf 'setpts=(PTS-STARTPTS)/8,fps=30' -an \
  -c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p \
  -map_metadata -1 -movflags +faststart \
  docs/media/grafana-production-monitoring-demo-8x.mp4

ffmpeg -v error -xerror \
  -i docs/media/grafana-production-monitoring-demo-8x.mp4 -f null -
```

Regeneration may change bytes; recheck the output and update the manifest before committing it.
