# Local Kustomize change review

The review command can compare real Kustomize builds from frozen local Git inputs. This is the bounded local-rendering part of F1. It does not establish Argo CD or Flux render parity, deployed state, network enforcement, or any expanded release qualification gate.

## Provision and check the tool

From the extracted plugin directory, using the configured Python environment:

```sh
python scripts/bootstrap_kustomize.py
python scripts/devsecops_review.py doctor --with-kustomize
```

The separate `kustomize.lock.json` pins Kustomize **v5.8.3**, published October 8, 2026. Its archive SHA-256 values are taken from the [official release](https://github.com/kubernetes-sigs/kustomize/releases/tag/kustomize/v5.8.3) and checked against the separately pinned official `checksums.txt` before a new archive is downloaded. Provisioning supports macOS and Linux on arm64 and amd64; runtime execution in this change was tested on macOS arm64. The receipt records the lock, checksum document, archive, and extracted executable hashes.

The default tool location is `~/.cache/devsecops/kustomize-v5.8.3/bin/kustomize`. An explicitly selected trusted executable outside the reviewed repository can be supplied with `--kustomize /absolute/path/to/kustomize`. The review records its actual version and executable hash. Declaring another executable does not qualify its version or a controller integration.

`bootstrap_kustomize.py --offline` reconstructs the executable only from a previously staged archive whose bytes match the public lock. It fails when that archive is missing or changed. This tool-cache operation does not prove the full disconnected software-factory workflow. Default `doctor` keeps the v0.1 prerequisites; use `--with-kustomize` to require this additional renderer. Review itself never provisions dependencies.

## Declare the frozen input boundary

Use a schema-version-2 trusted profile:

```json
{
  "schema_version": 2,
  "id": "application-review",
  "revision": "1",
  "targets": [
    {
      "name": "mission",
      "type": "kustomize",
      "source_root": "deploy",
      "path": "deploy/overlays/demo",
      "namespace": "demo",
      "kubernetes_version": "1.34.0"
    }
  ],
  "policy": {
    "require_image_digest": true
  }
}
```

`source_root` is a normalized repository-relative directory containing the selected overlay and every local base, component, resource, patch, and generator file. Both it and `path` must name explicit directories; repository-root `.` and traversal in profile paths are unsupported. Relative references such as `../../base` inside a kustomization are accepted only when the resolved dependency stays inside the declared source root. Kustomize's `LoadRestrictionsRootOnly` remains enabled, so ordinary file loads must also satisfy the renderer's own per-kustomization load restrictions. Shared declarations should be represented as local bases or components.

The whole declared tree is frozen independently for base and candidate. Existing file/byte limits, symlink and submodule rejection, ignored-file handling, and Git index protections still apply. A required dependency omitted by that snapshot becomes an explicit gap. A profile inside the repository is loaded from the immutable baseline commit; candidate edits cannot broaden `source_root`, select executables, or change policy. An external profile is explicitly trusted operator input.

```sh
python scripts/devsecops_review.py review \
  --repo /absolute/path/to/application-repository \
  --base BASE_COMMIT \
  --head CANDIDATE_COMMIT \
  --profile /absolute/path/to/trusted-profile.json \
  --output /absolute/path/to/new-review-directory
```

`WORKTREE` may be used for `--head`. Existing Helm and manifest profiles keep version 1 behavior. Version 2 permits mixed targets, but Helm-specific fields are rejected on Kustomize targets. Reviews with a selected Kustomize target emit report schema version 2; reviews of only existing target types continue to emit version 1. The published profile and report schemas explicitly accept both versions without widening the original version-1 target enums.

## Supported rendering and evidence

The local closure supports canonical Kustomization and Component directories, resources and local bases, namespace/name/label/annotation transformations, images and replicas, inline and local-file patches, local replacement files, transformer configuration files, and built-in ConfigMap/Secret generators with explicit local file inputs. Literal URLs in application data are allowed. Environment generator files must contain explicit values; bare keys that could inherit process environment are rejected. `kubernetes_version` selects schema validation; it does not replace Kustomize's embedded transformation definitions.

Kustomization, resource, patch, replacement, and transformer configuration YAML uses the existing strict parser before rendering; raw generator file contents remain application data. The traversal rejects cycles, more than 128 kustomization directories, dependency depth above 24, more than 10,000 tree entries, tree content over 64 MB, and individual dependency files over 8 MB. A directory must have exactly one of `kustomization.yaml`, `kustomization.yml`, or `Kustomization`.

Kustomize runs twice with isolated home/config directories, no Kubernetes credentials, plugins disabled, and the existing bounded shell-free process runner. Reports retain executable version/hash, per-run exit/duration/output hashes, the frozen input manifest, the resolved local closure's file hashes, and semantic hashes of both builds. Semantic comparison ignores document and mapping-key ordering while retaining actual value differences. Differing builds or incomplete dependencies suppress resource addition/deletion claims and yield `unknown`; confirmed independent findings remain visible.

Finding evidence identifies the rendered Kustomize output and JSON pointer. It does not pretend that a transformed field maps directly to a particular original patch line. Raw manifests, Secret data, and renderer diagnostics are excluded from exported evidence. Review decisions and exit codes remain `pass`/0, `fail`/1, and `unknown`/2.

## Explicit coverage limits

Remote Git bases and URI resources, executable/custom generators, transformer plugins, function annotations, Helm inflation fields, and custom OpenAPI/CRD configuration fields are rejected before rendering. Unknown kustomization fields also fail closed. Normal Kubernetes CRD resources can still be inventoried; existing scope/schema coverage rules determine their review gaps.

Unsupported inputs can be separately materialized and reviewed as ordinary local files before committing them into the declared tree. That establishes only the reviewed file contents, not the original transformation's provenance or controller parity. This slice does not resolve remote repositories, support Argo multi-source/ApplicationSet behavior, query a live cluster, run a pipeline, provision a factory, or qualify any hosted or human replay gate.

The test fixture under `tests/fixtures/kustomize` is synthetic and uses `registry.example.invalid`; it is a local rendering fixture and is not a deployable Iron Bank demo.
