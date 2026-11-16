# Original owned service route clients

`service_route` is an optional original `OwnedServiceRoute` object. It is never a
serialized address, proxy setting, URL-discovery response, or credential. The
fixed `owned_service_links.require_service_route(route, origin=...)` issuer must
re-admit its same-process journal, phase, owned process/listener, CA bytes and
absolute monotonic `deadline` and strict boolean `read_only` on every use.
`renew_service_route(route, context=...)` obtains a fresh phase binding from that
original coordinator. Explicit `operation_id=...` selects read-only reconciliation
of that exact existing unresolved operation. GET/HEAD are the only permitted
methods on a read-only route; renewal cannot upgrade it to mutation authority.

The selected URL remains the logical HTTPS origin. Clients connect only to the
capability's numeric `127.0.0.1` endpoint, without DNS or global proxy changes.
They preserve the original Host and TLS SNI and use exactly the selected CA.
Route and phase admission occurs before the socket, again after TLS before any
HTTP credential is sent, and before a response is accepted. Request deadlines
are capped by the original phase deadline. Redirects, retries and alternate
credentials remain forbidden by the underlying transports.

The optional keyword is supported by `HTTPSReadClient`, `EvidenceReader`,
`GitLabSession`, `GitReceiveClient`, registry collectors/qualification and the
base-possession/delivery path. `GitLabSession` renewal takes an explicit fresh
`service_route` while retaining its original credential, trust and irreversible
unknown-write latch. Writer cleanup obtains a new phase route through the
original coordinator. Neither renewal nor routing grants a mutation.

Canonical owned operations forward separate GitLab and registry capabilities.
Native Git still computes local objects and receive-pack bytes; the admitted
fixed HTTP receive client transmits the exact request. No native Git proxy is
needed. The isolated CI runner is a separate process and cannot restore this
Python capability; its explicit owned networking and trust configuration must
be established by the service-link coordinator before CI runs.

Tests use real disposable loopback TLS and controlled original-capability
boundaries. They establish transport behavior, not a live GitLab/registry demo,
issuer validity, lab readiness, end-to-end repetition or recording acceptance.
