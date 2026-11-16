# Isolated Linux and RKE2 engineering screens

This is an operator engineering helper for an explicitly authorized personal Linux devbox. Its default mode only boots Linux. The optional `--rke2-screen` installs the pinned public RKE2 distribution inside a separate guest. Neither mode is a canonical software-factory acceptance test.

From the repository root:

```sh
python3 scripts/lab_tcg_probe.py --host your-linux-lab --receipt work/v02/tcg-boot-probe-04.json
```

Use a new receipt path for every attempt. The selected host needs x86_64 Linux, existing Docker access, Ubuntu apt/keyrings, Python 3, OpenSSH, at least 20 GiB available memory, and 60 GiB free disk. The helper does not install host packages or use host sudo.

Acquisition resolves the official Ubuntu container digest, verifies the dated Ubuntu cloud image against Canonical's signed checksums, and downloads exact requested QEMU package versions plus dependencies using a new private apt configuration. Docker builds from these artifacts with networking disabled. The receipt records the image IDs, package and metadata hashes, and measured result.

The guest receives four emulated CPUs, 8 GiB RAM, and a 40-GiB sparse disk. Its unprivileged QEMU container receives a four-CPU quota, a 9-GiB memory ceiling including emulator overhead, a process limit, a read-only root filesystem, no capabilities, and no-new-privileges. The sole host bind mount is the new private lab directory. QEMU uses user-mode networking inside a dedicated internal Docker network.

The helper requests an ephemeral loopback-only SSH publish. On Docker versions that suppress publishing on internal networks, it verifies the container's exact network identity and private address, then connects from the devbox directly to that internal endpoint. It never substitutes a public host bind or external network. SSH requires the per-run client identity and pinned guest host key. The guest has no external network route; this is a boot probe, not an artifact-fetch or registry-access test.

After the ten-minute boot gate, the helper measures guest identity, clock consistency, and a bounded SHA-256 workload. It always attempts to stop and remove only the exact recorded containers and network, after checking ownership labels. It does not prune Docker resources. Successful boot and confirmed cleanup are both required for `boot_probe_pass`.

Each receipt includes a private remote lab path. The remote `ssh/operator` file is the retrievable private SSH key; `ssh/known_hosts` pins the guest host identity. The private directory also retains the guest disk, signed cloud-image metadata, seed, build artifacts, state, and logs that were actually created. These may contain credentials and must not be committed or copied into presentation reports. The sanitized receipt exports paths and measurements, not key or cloud-init contents. Application credentials are managed separately by `factory-demo init-credentials` and are not read here.

A missing receipt requires reconciliation of the remote state before another attempt. No automatic resume or platform success is inferred. The helper deliberately preserves tooling images and private files after runtime cleanup for diagnosis; deletion of those retained artifacts is a separate scoped action.

See the [reference driver decision](../../../../docs/demo/reference-driver-decision.md) for the proposed RKE2/Big Bang stages and their still-open gates.

## Optional RKE2 screen

```sh
python3 scripts/lab_tcg_probe.py --host your-linux-lab --rke2-screen --receipt work/v02/tcg-rke2-screen-01.json
```

This mode requires 44 GiB available host memory and 310 GiB free disk; it rechecks both immediately before runtime creation. The guest has 8 CPUs, 32 GiB RAM and a 160-GiB sparse disk, with an 8-CPU/34-GiB host container ceiling. [rke2-release.json](rke2-release.json) records the exact public RKE2 release and the independently inspected Big Bang chart compatibility constraint. These are candidate inputs, not compatibility qualification.

The host downloads and verifies all RKE2 archives against official HTTPS release metadata digests and release checksums. This is not a cryptographic release-signature claim. The guest receives and re-hashes those archives over pinned SSH before the 1,200-second readiness gate starts. The official installer runs in guest-only local-artifact mode. Canal is selected; the bundled ingress controllers are disabled in preparation for a separately designed Big Bang ingress. Three consecutive authenticated API/node observations must report the exact server and kubelet version, followed by an actual authenticated API discovery read. This screen does not test DNS, NetworkPolicy enforcement, storage durability, Big Bang or Registry1.

Issued kubeconfig, server token and API CA are retained privately in the remote lab `operator/` directory (0700 directory, 0600 files). Only paths and the nonsecret CA hash appear in the sanitized receipt. No application credential vault is read. The helper stops the guest service and attempts guest shutdown before exact runtime cleanup. A successful readiness result still requires confirmed container and network removal. The retained guest disk is inactive after cleanup; its credentials do not imply a currently reachable API.

The screen explicitly pins RKE2 `node-ip` to the fixed guest address `10.0.2.15`. Restricted QEMU networking can still advertise an IPv6 default route; automatic address selection must not change an existing etcd member's peer identity on reboot. This pin does not add a default route or change network isolation.

Retained adoption checks the actual guest address, original CA, exact screen configuration, pinned RKE2 service bytes, effective unit, and absence of config/environment overrides immediately after pinned SSH becomes available. Only the exact original screen config may receive the missing `node-ip` key. Its original bytes and before/after hashes are saved privately, followed by one bounded native service restart request. Already pinned guests do not restart. An interrupted migration remains explicitly incomplete and requires reconciliation; it is not silently retried. The original closed cloud-init seed must match the guest, with no vendor user-data. Cloud-config, cloud-final and RKE2 unit state, dependencies and monotonic timings are captured without waiting for cloud-final. All work remains inside the unchanged twenty-minute readiness gate, and API/cluster identity checks still decide readiness.

The pinned RKE2 release includes an 11-byte `rke2-server.env` containing `HOME=/root` and a final newline. Retained adoption accepts those exact bytes only at `/usr/local/lib/systemd/system/rke2-server.env` (SHA256 `2584cb4fcddb07782af3e50f2ee4200d0b84e64856f3db458bc0b1da53a70b79`), alongside the existing absent, empty or comment-only file allowance. Other assignments, duplicate variables and assignments in either `/etc` environment file remain rejected. The guard reads bytes without evaluating or sourcing them; observations include only admitted paths, byte counts, hashes and admission categories. The fixture comes from the exact official release source, not a claimed capture of the retained guest.
