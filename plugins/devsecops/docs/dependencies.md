# Dependency inventory

The public [`uv.lock`](../uv.lock) records exact Python dependencies and artifact
hashes. This table is metadata observed in the qualified Python 3.11 environment;
platform-specific selection can differ. Upstream license files remain in the
installed distributions. The plugin ZIP contains source and lockfiles, not these
third-party wheels or executable environments.

| Distribution | Version | Reported license |
| --- | --- | --- |
| attrs | 26.1.0 | MIT |
| iniconfig | 2.3.1 | MIT |
| jsonschema | 4.25.1 | MIT |
| jsonschema-specifications | 2025.9.1 | MIT |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pluggy | 1.6.0 | MIT |
| Pygments | 2.21.0 | BSD-2-Clause |
| pytest | 8.4.2 | MIT |
| PyYAML | 6.0.3 | MIT |
| referencing | 0.37.0 | MIT |
| rpds-py | 2026.9.1 | MIT |
| ruff | 0.14.0 | MIT License |
| typing_extensions | 4.16.0 | PSF-2.0 |
| zstandard (v0.2 preview) | 0.25.0 | BSD-3-Clause |

PyYAML, jsonschema and their transitive requirements are runtime dependencies;
the v0.2 preview also requires zstandard for official-format Grype database archives.
Pytest, Ruff and related packages support development/testing; installed runners
use an external environment with `uv sync --frozen --no-dev --no-install-project`
and the installed direct script entry point, as shown in the README.

Helm and Kubeconform binaries and Kubernetes schemas are separately provisioned
from the origins and hashes in [`toolchain.lock.json`](../toolchain.lock.json).
They are not included in the plugin ZIP. Review their upstream notices under the
organization's software-distribution process before redistributing a tool cache.

The source inventory records that the implementation is original and no VoltAgent
source file is incorporated. This community contribution preview uses Apache-2.0,
matching the target repository; see [LICENSE](../LICENSE) and [NOTICE](../NOTICE).
This source license does not establish publisher verification, marketplace review,
customer approval, or live qualification. Third-party dependencies retain their
own licenses and are provisioned separately.
