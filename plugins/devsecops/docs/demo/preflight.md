# Inspect the demo configuration

The Software Factory runtime is under development. This command performs **local preflight only**:

```sh
python /absolute/path/to/devsecops/scripts/factory_demo.py doctor \
  --profile bigbang-live \
  --config /absolute/path/to/operator-demo-config.json
```

The installed console equivalent is `factory-demo doctor`. Install the plugin's pinned Python
dependencies first using its existing bootstrap instructions. Run from any directory; the wrapper
resolves its own package assets.

Copy `demo/software-factory/config/demo-config.example.json` outside the plugin source. Replace
the example hostnames, owner, expiry, project IDs, namespaces and node inventory with approved lab
values. Leave `allowed_mutations` empty while inspecting configuration. Installing or inspecting
the plugin never authorizes a production cluster change.

The example uses environment-variable references, not credential values. Obtain the actual
credentials through the approved operator authentication process. Separate read-only service
identities from the scoped lab operator identities. Never paste a token into a URL, JSON config,
command argument or evidence bundle. Credential presence alone does not prove authentication or
permissions; the current command explicitly reports that limitation.

Once real image acquisition and compatibility checks produce a lock, add `--lock /absolute/path/to/lock.json`.
The lock must bind the packaged scenario and assertions, exact tool hashes, platform versions,
authenticated image digests, individual required attestations and trust material. No example live
lock is supplied, because an invented digest would undermine the demo's evidence.

The canonical runner selection is `owned-bootstrap-v2`, with a pinned
`services.runner_vm` file using the `owned-runner-vm-v1` contract. Prepare the
actual approved boot image, tools, package, service trust and guest account from
[the runner VM input contract](../interfaces/owned-runner-vm.md). The disposable
runner guest is separate from RKE2 and runs as `gitlab-runner`, UID/GID 10001;
the legacy container-runner recipe cannot qualify the complete demo. A recipe
pin alone does not establish a running guest, registered runner or working CI.
The guest selection stays inside the existing `owned_bootstrap_recipe` auxiliary
input; the six main inputs, sixteen auxiliary inputs and 33 phases are unchanged.

Before recording, verify that the selected grants cover the whole run and
cleanup. Keep the original resident process available for the explicit
[cleanup controls](../owned-cleanup-continuation.md). Normal continuation and
restricted guest disposal are different admitted states, each requiring the
displayed fresh command and current scoped authorization. None of these local
preflight checks authorizes provisioning or resolves an existing uncertain action.

Exit 2 currently reports missing live checks or unfinished lifecycle work. This is expected for
the example configuration. The output includes actionable gaps and never reports the factory as
ready from local configuration alone. The `e2e` command exposes the fixed `owned-linux` backend with explicit pinned
production selections and the resident `codex-cli` driver. Those source paths
still require installed-package qualification, authorized live service checks and
actual repeated runs; consult [the lifecycle contract](../interfaces/factory-lifecycle.md)
and [implementation ledger](implementation-status.md).
