# Synthetic mission-status application

This small demonstration app generates non-sensitive synthetic events. It uses no
customer source, data, endpoints, imagery, or branding. It is a factory-built
application intended to use a separately verified Iron Bank Python base; the
derived image does not inherit Iron Bank approval.

The app provides two **different TCP listeners**:

| Listener | Route | Behavior |
| --- | --- | --- |
| Health, port 8080 | `GET /health` | JSON `status: ok`, `service: mission-status`, `data: synthetic` |
| Metrics, port 9090 | `GET /metrics` | Prometheus text containing `mission_events_total` and `mission_last_event_timestamp_seconds` |

Each route returns 404 on the other listener. HEAD is supported; mutation methods
return 405. Responses disable caching. There is no authentication, TLS, persistent
state, customer input, or intended public exposure: access belongs to the
qualified lab's ingress, mesh and network policies. No process/environment
collectors or caller-controlled metric labels are registered; request contents
are not logged.

An independent producer creates one synthetic event at startup and approximately
once per second. It keeps running even when no one scrapes metrics. The counter
resets when the process restarts. The timestamp gauge describes a generated
source event; it does not replace Prometheus sample timestamps, target identity,
or the scenario's required freshness and observation-window checks.

The app deliberately has **no telemetry-failure switch**. In D2, the qualified
CNI/mesh policy must block only the intended scrape path while `/health` stays
reachable. Application tests do not prove NetworkPolicy enforcement, mesh
authorization, Argo delivery, or recovery. Keep the image unchanged during the
fault and repair.

## Run locally

Install the one pinned dependency in an isolated environment with hash checking:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes --only-binary=:all: -r requirements.txt
.venv/bin/python app.py
```

The default bind address is loopback. `--host 0.0.0.0` is used by the container.
The two ports and event interval can be set explicitly; ports must differ.
Port 0 is available for isolated local tests. SIGTERM/SIGINT close both listeners
and the producer. A failure to bind either listener fails startup and releases
the other listener.

## Container inputs and qualification boundary

The Dockerfile requires **both** `IRONBANK_PYTHON_REPOSITORY` (the repository path
under `registry1.dso.mil/ironbank/`) and `IRONBANK_PYTHON_SHA256` (64 lowercase hex
characters from authenticated digest resolution). It has no default base, mutable
tag, or public-image fallback. Obtain these values from the qualified factory
lock. A syntactically valid digest is not proof of Iron Bank provenance: the
separate acquisition gate must verify the origin, exact platform, required
signatures/attestations, approved trust, and base runtime behavior before a build.

The base must provide Python 3.11+ with working `venv`/`ensurepip`; the build must
permit the explicit root install step and the selected runtime UID/GID 10001.
The final image runs nonroot and needs no application filesystem writes. Validate
its security context, read-only filesystem, mesh, admission and platform-specific
UID constraints on the selected profile. No base image or architecture is
qualified by these files, and no container build/deployment has been proved yet.

Prepare a wheelhouse as a separate authorized acquisition step:

```sh
python3 -m pip download --require-hashes --only-binary=:all: --no-deps \
  --dest wheelhouse -r requirements.txt
```

The Docker build installs only from that wheelhouse with networking disabled and
hash checking enabled. The build driver must validate and supply the qualified
base arguments and registry credentials through approved credential references,
never build arguments. A disconnected builder needs the declared Registry1
origin mapped to its qualified internal mirror, with preserved digest identities
and offline verification material; it must not substitute another base.

The dependency's [public PyPI release](https://pypi.org/project/prometheus-client/0.26.0/)
and the exact artifact/hash are recorded in `dependency-lock.json`. Its downloaded
bytes were checked against PyPI metadata. That receipt is an integrity observation,
not a signature, scanner result, or approval.

From the plugin root, run `python -m pytest tests/test_mission_app.py`; the root
development lock supplies the same pinned client version. These are real local
HTTP/lifecycle tests, not live factory acceptance receipts.

## Independent HTTP probe

The image also contains the standard-library-only `probe.py`; the normal app
entry point and its `/health` and `/metrics` routes are unchanged. A separately
authorized operator can run:

```sh
/opt/mission-status/venv/bin/python /opt/mission-status/probe.py \
  --request /mounted/http-probe-request.json
```

The request must explicitly bind the run/attempt, actual source Pod identity,
destination namespace/Service/port/path and independently obtained ClusterIP(s).
Configure `DEVSECOPS_PROBE_POD_NAMESPACE`, `DEVSECOPS_PROBE_POD_NAME` and
`DEVSECOPS_PROBE_POD_UID` from the Downward API. The probe sends one bounded GET
only after those claims match the request, DNS equals the declared address set,
and the connected peer is allowed. It has no Kubernetes credentials, discovery,
proxy, redirect, retry or mutation path. HTTPS is explicitly unsupported in this
slice; no downgrade occurs.

Its JSON receipt separates the narrow HTTP observation from unverified source
claims and policy/recovery decisions. Exit 0 means only the expected HTTP response
was measured; it does not mean recovery or policy enforcement succeeded. The
authorized harness still needs independent source/target/image corroboration and
positive/negative controls. See the [HTTP probe contract](../../../../docs/interfaces/http-probes.md)
and the packaged request/receipt schemas. `tests/test_http_probe.py` exercises
real local HTTP through Python-only test hooks that always label their receipts
as controlled tests. No container build or live control matrix is claimed.
