# Explicit KVM acceleration on an owned Linux host

Fresh RKE2 and dedicated runner VM recipes can explicitly select KVM on a
previously authorized Linux x86_64 host. Add the same selection to both recipes:

```json
"acceleration": {"kind": "kvm", "device_group": 108}
```

`108` is illustrative. Select the actual numeric group of `/dev/kvm` on the
approved host. The device must be a character device, major/minor `10:232`,
owned by root and that nonzero group, with mode `0660`. The operator must already
have nonroot access. This component does not install KVM, load a kernel module,
change host permissions or provision a host. Hardware or nested virtualization
must be enabled by the authorized host operator beforehand.

Omitting the field preserves the existing TCG recipe and binding. Explicit null,
unknown fields, a different accelerator and a root or malformed device group
are rejected. The foundation's existing `fresh-owned-tcg-v1` wire kind remains
for compatibility; the optional field selects its actual accelerator. Retained
TCG foundations do not acquire KVM support or a new adoption path.

Before creating persistent resources, the pinned admission helper opens the
device without following a symlink, verifies its stable identity, requires KVM
API version 12, creates one empty VM descriptor, and closes both descriptors.
This follows the [Linux KVM API](https://docs.kernel.org/virt/kvm/api.html).
The resulting observation establishes device access, not a booted guest or
working cluster. An unavailable device fails; there is no fallback to emulation.

Only the QEMU runtime container receives
`--device /dev/kvm:/dev/kvm:rw --group-add <selected-group>`. Seed containers
remain device-free. The exact device, permissions, group and `-accel kvm -cpu
host` arguments are checked again against the observed container. Additional
devices, mknod access, extra groups or a TCG command invalidate the observation.
[Docker documents these device and group controls](https://docs.docker.com/engine/containers/run/).
The nonroot user, dropped capabilities, no-new-privileges, read-only root,
bounded resources and restricted networking remain enforced. Docker itself is
still privileged host infrastructure.

The helper's bytes are included in the installed package and each remote source
binding. KVM admission is recorded with the original generation. Cleanup uses
that original ownership and exact container inventory; it does not require a
new KVM admission or a working device to stop an already observed runtime.
No saved admission record authorizes adoption, another run or host mutation.

Acceptance requires an actual boot, the existing fixed readiness and execution
budgets, independent recovery measurements, replay and observed cleanup. Local
parser, kernel-mock and lifecycle tests alone do not qualify a Linux KVM run.
