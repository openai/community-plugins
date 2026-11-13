# Rootless executor qualification

`scripts/qualify_rootless_builder.py` executes one fixed offline fixture inside a fresh, explicitly owned Linux rootless BuildKit instance. It starts no Docker daemon, uses no registry credentials, and makes no system configuration changes. This prerequisite check is separate from the Iron Bank mission build and the canonical Software Factory scenario.

The fixture starts from scratch, copies the exact pinned static RootlessKit executable, and executes `rootlesskit --version` in a real BuildKit `RUN --network=none` step. Both the daemon and fixture have isolated networking. The client uses the built-in Dockerfile frontend, a fixed recipe, a fresh private state directory and `--no-cache`; it does not accept candidate Dockerfiles, secrets, SSH forwarding, extra entitlements, registry destinations or alternate mission images.

## Required execution environment

Use the explicitly authorized guest and its non-root operator UID. The guest needs root-owned `newuidmap` and `newgidmap` executables with their normal setuid permissions, one non-overlapping range of at least 65,536 subordinate UIDs and GIDs for that operator, and permission to create user namespaces. RootlessKit, BuildKit, buildctl and buildkit-runc must match `supplychain.lock.json` exactly. Stage them using `bootstrap_supplychain.py --platform linux-amd64 --builder` or the corresponding ARM64 platform before entering an offline environment.

The qualifier never installs packages, edits `/etc/subuid` or `/etc/subgid`, changes sysctls, or loads AppArmor policy. Missing prerequisites produce exit 2 with a specific error code. If Ubuntu's AppArmor policy requires an exception, prepare a temporary profile for the exact copied RootlessKit path in this owned guest and retain the separate setup/removal receipt. Do not change the outer host or disable its policy globally. A namespace alone does not prove the later registry or egress-policy qualification lanes.

## Run and evidence

Choose a **new** private absolute run path of at most 70 bytes using letters, digits, periods, underscores, hyphens and slashes. This protects Unix socket limits and avoids ambiguous CLI exporter paths. Supply the actual guest UID and staged tools directory explicitly:

```sh
python3 "$DEVSECOPS_PLUGIN_ROOT/scripts/qualify_rootless_builder.py" run \
  --output /tmp/devsecops-builder-qual-01 \
  --tools "$PINNED_LINUX_EXECUTOR_TOOLS" \
  --owner-uid "$GUEST_OPERATOR_UID" \
  --timeout 600
```

The operation deadline is bounded to 30–600 seconds, followed by at most 30 seconds for normal cleanup. It records an intent before process startup and maintains a private process journal. Process identity includes kernel boot, PID/start time, executable inode and exact command hash. Cleanup opens a Linux pidfd, repeats the identity check, and signals through that descriptor, preventing numeric PID reuse between checking and signaling. Unsupported pidfd operation fails preflight. It verifies the real socket peer and worker before and after the build, requires the expected execution output, and checks the OCI archive's index, platform manifest, config, every layer blob and uncompressed layer identity against build metadata.

The terminal `qualification.json` reports `pass` only after those checks and cleanup succeed. Exit 0 means this narrow executor probe passed. It explicitly retains `canonical_demo: not-run`, `mission_build: not-run` and `registry_trust: not-tested`. Exit 2 means evidence is incomplete; inspect the retained gap code and private logs. The harness hash identifies the qualifier module; retain the caller's verified package/source identity and the overlay hash separately when running a development overlay.

Successful cleanup removes the exact owned processes, socket, copied tools, fixture and runtime directories. It retains the OCI artifact, build metadata, private command logs, lock, intent, process history and cleanup receipt. The output is evidence to review, not a production image to promote.

## Interrupted runs

SIGINT and SIGTERM trigger bounded cleanup. A killed host process or ambiguous resource identity can leave an incomplete journal; do not start another run in the same directory or kill processes by name. Reconcile that exact run with:

```sh
python3 "$DEVSECOPS_PLUGIN_ROOT/scripts/qualify_rootless_builder.py" cleanup \
  --output /tmp/devsecops-builder-qual-01
```

Cleanup refuses changed PID/start-time, command, boot, socket or directory identities. It checks kernel process state before claiming no owned processes remain. Untracked surviving processes produce an explicit incomplete result rather than a false cleanup pass. A completed cleanup clears active process identities while retaining history, so later PID reuse cannot cause an unrelated process to be signaled.

## Qualification status

Controlled tests cover process reuse, ownership, subordinate-range overlap, output bounds, descriptor-based cleanup, interrupted-state boundaries and required execution/artifact/cleanup observations. These are not live executor evidence. The real owned-guest run remains pending the retained RKE2 guest's adoption and durability qualification.

Primary implementation references: [BuildKit rootless mode](https://github.com/moby/buildkit/blob/v0.34.1/docs/rootless.md), [BuildKit CLI](https://github.com/moby/buildkit/blob/v0.34.1/docs/reference/buildctl.md), and [RootlessKit 3.3.0 CLI/network implementation](https://github.com/rootless-containers/rootlesskit/blob/v3.3.0/cmd/rootlesskit/main.go).
