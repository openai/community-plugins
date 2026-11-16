# Fixed owned CI package admission

The two installed jobs use `runner_package.py`: `publish_owned_mission.py`
selects `installed_publication_package`, and `validate_owned_deployment.py`
reaches `installed_deployment_package` through `deployment_validation`.
Each checks the package before its work and again before publishing success.
These are fixed CI entry points. The desktop Git composition and native Codex
installation still require the versioned Codex cache; there is no automatic
fallback between the two installation layouts.

The preprovisioned Linux amd64 guest supplies the original `gitlab-runner`
account, UID and GID 10001, without additional groups, and home
`/home/gitlab-runner`. The selected Python is at least 3.11 and the jobs invoke
it with `-I -B`, with no external `sys.pycache_prefix`. The original runner plan
selects `DEVSECOPS_PLUGIN_ROOT` and `DEVSECOPS_PYTHON`. The package root must be
strictly beneath `/opt/devsecops`; the interpreter must be separately located
beneath that prefix, and its resolved executable must remain outside the
plugin. For example, sibling directories named `package` and `runtime` express
that topology; these are structural examples, not qualified image paths or
pins. Root-owned interpreter symlinks are admitted only through bounded,
root-owned, non-writable link and target ancestry.

The actual loaded engine root must equal the selected package root. Package
files and all containing directories are root-owned and cannot be written by
the runner or its groups. The invocation's manifest hash, qualified plugin ID
and version must match the actual package. Reads use bounded regular no-follow
file descriptors and compare identity before and after reading. Admission
checks every manifest member and exact file membership, with no symlinks,
nonregular objects, bytecode caches or `.pth` files, including otherwise
manifested caches. The bound is 1024 members, 1024 directories, 16 MB per member
and 128 MB in aggregate, matching the guest supervisor's package envelope.
The fixed entry script, shared argument parser and all already-loaded
`devsecops` modules must map to the selected manifest and current source bytes.
The caller's original deadline applies throughout; no package admission renews
it.

This verifier reads only. It does not install or update the package, modify
ownership or permissions, obtain credentials, grant remote effects, or restore
resident host authority. Its local controlled tests do not qualify a guest
image. A real prebuilt image containing the final package and dependencies,
followed by authenticated jobs on the original owned runner and live recording
acceptance, remains necessary.
