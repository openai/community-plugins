# Owned fresh service connectivity

The canonical fresh path keeps QEMU `restrict=on` and the original internal Docker
network. It adds exactly two guestfwd destinations: `10.0.2.100:8444` for the
selected GitLab origin and `10.0.2.101:8443` for the selected registry origin.
The command and stdio relay source are fixed by the installed package. The
existing offline seed container checks Python 3.10+ and its required stdlib before
QEMU starts. It installs no tools and acquires no images.

`OwnedServiceLinks` is constructed from the exact original Linux driver, fresh
foundation, Zot component and service stack. Cleanup registers the component
before its first effect. `up(context)` starts fixed remote relays, installs the
guest mappings/trust, verifies the original guest and opens original localhost
SSH forwards. `configure_runner(context)` runs only after the original service
stack has observed the runner container ID. It verifies the frozen staged CA
files, changes only that container's hosts file and checks the registry TLS
origin and unauthenticated 401 challenge. Shell-executor child processes use the
same container namespace and the already staged service CA files.

Remote listeners bind only the original fresh bridge gateway, service bridge
gateway and loopback. Each connection rechecks the selected target Docker ID,
labels and single original network. Nonlocal peers must be the original guest
runtime or runner container at its independently observed bridge address. The
TLS stream remains encrypted through the relay. Clients use the selected
logical hostname for SNI/Host and the pinned original CA; no global DNS, host
trust, broad network attachment, plaintext service endpoint or TLS bypass is
introduced.

Guest changes preserve original file bytes and the observed CoreDNS ConfigMap
UID/data. The guest helper uses compare-and-swap checks, the normal RKE2 restart,
and original cluster UID/readiness, DNS and TLS observations. The bootstrap
compiler separately supplies exact CA trust to Flux and Argo. Run-created public
CA copies are removed and original guest mappings/configuration restored before
relay shutdown. Runner mappings are restored before the runner is removed.

The route API is resident and nonserializable:

- `route(context, service)` issues a normal route for `gitlab` or `registry`.
- `require_service_route(route, origin=...)` reaudits the original phase, source,
  endpoint, CA and local SSH process/control socket before and after client I/O.
- `reconciliation_route(context, service, operation_id)` and
  `renew_service_route(route, context=..., operation_id=...)` allow only the exact
  existing unknown journal operation. Such routes remain read-only; every client
  send boundary restricts them to GET/HEAD. Renewal never upgrades an existing
  read-only capability or reconstructs one from a receipt.

The original configuration expiry bounds the daemon and each issued route. A
certificate renewal preserves SSH transport identity but does not extend this
service-route grant. Select an original window covering the complete two-hour
run and cleanup. After that window, API cleanup requiring a route remains blocked
until a supported fresh scoped route grant exists; this implementation never
edits frozen input expiry or claims expired cleanup succeeded.

Every effect has an original intent. Reconciliation only reads. A lost response
may establish `cleanup_eligible` from positive original partial-state readback
without declaring the environment ready. `continue_cleanup(context,
prior_operation_id)` requires a new down operation and the original terminal
reconciliation; a still-unresolved signal is never repeated. Relay shutdown
observes the original process/session and its child process group absent. Public
run-created source, ownership and reconciliation audit files are deliberately
retained as evidence; their receipts identify that retention explicitly.

Controlled tests cover these source contracts. They do not establish live guest
networking, a successful GitLab pipeline, fresh-environment repetitions, or a
playable recording. Those remain installed-package acceptance gates.

Reference behavior: QEMU's documented fixed
[guestfwd command form](https://www.qemu.org/docs/master/system/invocation.html)
and RKE2's [private registry configuration and restart requirement](https://docs.rke2.io/install/private_registry).
