# Limitations and coverage

DevSecOps v0.1 reviews Kubernetes and Helm changes using declared inputs and local
tools. It helps an engineer assess a change; it is not a deployment controller or
a complete DevSecOps platform.

## What a result establishes

A successful required check establishes only that the selected tool/check
completed for the recorded inputs. The report binds the repository revisions,
profile, targets and tool evidence so a reviewer can understand that scope.
Completion and decision are separate: `complete` can contain findings; `partial`
or `error` must not become a passing result.

Unselected targets, excluded files, missing dependencies, unknown custom-resource
schemas and unsupported inputs are not implicitly reviewed. A green GitLab
pipeline does not expand local coverage or prove deployment safety.

## What it does not establish

- Kubernetes API-server admission, admission-webhook behavior or live API discovery.
- Live object drift, controller reconciliation, rollout health or runtime recovery.
- CNI enforcement, actual network reachability or effective network isolation.
- Cloud load-balancer routing, external traffic policy, storage availability or
  complete workload blast radius.
- Full Helm runtime behavior, including cluster-dependent `lookup` values and
  arbitrary post-renderers or hooks.
- Image vulnerability status, image publisher trust or a complete software
  supply-chain assessment merely because an image uses a digest.
- Complete application-code security review, CIS/STIG compliance, an ATO,
  FedRAMP authorization or customer approval.
- An operating-system sandbox around the review subprocesses. Codex and the host
  must enforce the approved filesystem/network execution boundary.

## Rendering and schema limits

Render context is declared: release name, namespace, values, Kubernetes version,
API capabilities and install/upgrade mode. Helm template evaluation can depend on
time or random functions; identical source is not a guarantee of deterministic
rendered output. Review unusual templates before relying on their diff.

Chart dependencies must be present locally. The workflow does not download or
update dependencies, run deployment commands, execute hooks/post-renderers or
contact a cluster to fill in missing information. Local schema validation checks
structure and types; it does not reproduce Kubernetes admission and controllers.

## Profiles and policy

Profiles are review data, not credentials or authorization. A customer profile
can express image-registry, digest and labeling expectations, but a matching
profile name does not demonstrate that the customer approved those expectations.
The Lockheed example is synthetic configuration requiring owner review.

A repository profile comes from the immutable baseline, not candidate-controlled
bytes. Explicit external profiles are separate trusted inputs. Severity overrides
and expiring exceptions can affect the finding gate, but remain visible in the
report and cannot excuse schema failures or missing required evidence. The engine
checks exception shape and expiry; it cannot verify that an owner authorized the
exception. See [policy configuration](policy.md).

Static checks target bounded common risks. Their absence is not proof that all
risks were found. A finding may require topology, workload purpose, controller
semantics or approved exception context to resolve.

## Sensitive data

The review avoids publishing rendered Secret payloads and raw tool diagnostics.
It is not a comprehensive secret scanner or data-loss-prevention product. Resource
names, paths, policy data and source metadata can be sensitive. Treat reports as
customer engineering material. Never pass credential values in CLI arguments or
profile fields.

## Supported deployment surface

The plugin uses local Python and CLI tools in the Codex host environment. An
account upload does not make those executables available on web or mobile.
Local execution does not imply offline model inference, customer VPC deployment,
air-gap support, data-residency guarantees or an authorization to operate.

Existing Codex Security, SonarQube, GitHub and Production Monitoring plugins are
separate products. This package does not bundle, require or establish customer
access to them. Their optional integration would need separate implementation and
verification.
