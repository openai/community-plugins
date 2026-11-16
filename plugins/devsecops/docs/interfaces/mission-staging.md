# Mission staging and immutable readback

`scripts/stage_mission.py` qualifies the configured Iron Bank base, executes the fixed mission build, uploads the resulting image to one separately selected staging repository, and pulls every uploaded byte back by digest. It runs those steps in one process. A saved build or staging report cannot authorize a later job to upload, sign or promote an image.

This is an explicit write operation. Use a dedicated staging account with `pull,push` permission on only the selected repository. The existing review and registry evidence clients remain GET-only. Registry1 is rejected as a staging destination.

## Operator configuration

Supply the [build configuration](mission-executor.md) and a separate [staging configuration](../../demo/software-factory/ci/mission-staging.example.json), each with an independently selected SHA-256. The example uses a reserved, non-working hostname; replace it with the approved staging origin and repository. The configurations must be kept outside the untrusted application checkout and supplied by the runner operator. CI must also pass its actual checkout and immutable commit through the expected-source arguments.

The staging document has exactly these fields:

| Field | Meaning |
|---|---|
| `schema_version`, `kind` | `1`, `mission-staging-configuration` |
| `origin` | Canonical HTTPS origin, no trailing slash, credentials, path, query or fragment |
| `repository` | One exact repository; no tag or wildcard |
| `credentials` | Dedicated environment-variable reference names, never credential values |
| `ca_bundle` | Explicit absolute CA bundle path, or `null` for system trust |

Use `{"token_env":"MISSION_STAGING_WRITE_TOKEN"}` for a supplied bearer token. For a registry using Basic credentials to obtain scoped bearer tokens, use this complete object:

```json
{
  "basic_username_env": "MISSION_STAGING_USERNAME",
  "basic_password_env": "MISSION_STAGING_PASSWORD",
  "token_path": "/service/token",
  "token_service": "staging"
}
```

The auth realm must match the configured path on the same HTTPS origin. The write token requests only `repository:<selected-repository>:pull,push`. Readback performs its own GET-only authentication, requesting `pull`. Set both token fields to `null` only for a registry accepting Basic authentication directly. Use the registry's actual service/path, not values copied from the example. A registry requiring a different auth origin or off-origin blob storage is unsupported by this implementation.

The runner supplies `DEVSECOPS_PYTHON`, an approved interpreter with the plugin's external dependencies, and `DEVSECOPS_PLUGIN_ROOT`, the verified immutable installation. Invoke:

```sh
"$DEVSECOPS_PYTHON" -B "$DEVSECOPS_PLUGIN_ROOT/scripts/stage_mission.py" \
  --build-config "$MISSION_BUILD_CONFIG" \
  --build-config-sha256 "$MISSION_BUILD_CONFIG_SHA256" \
  --staging-config "$MISSION_STAGING_CONFIG" \
  --staging-config-sha256 "$MISSION_STAGING_CONFIG_SHA256" \
  --expected-source-repository "$CI_PROJECT_DIR" \
  --expected-source-revision "$CI_COMMIT_SHA" \
  --runtime-root "$MISSION_RUNTIME_ROOT" \
  --output "$MISSION_DELIVERY_OUTPUT"
```

Both output directories must be new. The existing GitLab/Jenkins recipes remain build-only; adopt this explicit delivery command only in the separately authorized staging job. Jenkins supplies `WORKSPACE` and `GIT_COMMIT` in place of the GitLab variables. This command returns zero only for the narrow qualification/build/staging/readback chain. `factory_decision` stays `unknown`; scanning, signing, promotion, deployment and recovery are separate steps.

## Upload and readback contract

The consumer requires a live, unexpired `BuiltMission` from the actual executor. It freezes and validates the OCI archive, all descriptors, compressed layer digests and decoded filesystem digests before network activity. It rechecks run/source/architecture authority before every mutation and after final publication.

The transport implements the OCI [POST then PUT blob upload](https://github.com/opencontainers/distribution-spec/blob/v1.1.0/spec.md#post-then-put), followed by a manifest PUT at its SHA-256 digest. It preserves bounded opaque upload query parameters but rejects cross-origin or cross-repository locations, path encoding, duplicate query keys, redirects, mounts and tag routes. It sends no PATCH or DELETE requests. TLS verification is mandatory; proxy environment variables are ignored.

Each write has a private, fsynced `mutation-NNN.json` intent before the request starts. A dropped connection, invalid acknowledgment or deadline can mean the registry already stored bytes. Such outcomes remain `unknown`; the writer never retries or deletes them. Preserve the run directory, inspect the exact immutable digests using a read-only account, and let the registry's normal incomplete-upload retention policy handle abandoned sessions. An intent says a write *may* have happened; it is not confirmation.

Successful HTTP acknowledgments are still incomplete evidence. The GET-only image-layout collector independently fetches the exact manifest, config and all layers, checks sizes/digests/platform/decompressed digests, and retains the complete OCI layout. The final result binds the build, source/context, base, staging config, destination and actual retained readback. A process-local `StagedMission` is issued only after terminal file, route, deadline and authority checks. Consumers must revalidate retained bytes before using them.

Bounds are 512 MB for the source OCI archive and cumulative upload bytes, 128 layers, 264 upload/auth requests, 136 readback/auth requests, 64 KB per token response, and at most 120 seconds per network request. Staging has a default 600-second and maximum 900-second deadline; the combined command defaults to 2,400 seconds and caps at 2,700. Existing F0/build authority expiry still applies and is never extended. Partial files and write intents remain evidence when work times out.

## Validation limits

The test suite uses real local TLS, ephemeral credentials, OCI tar/gzip content, actual POST/PUT storage and independent authenticated GET readback. It exercises corrupted bytes, redirected uploads/readback, scope changes, partial writes, aggregate byte bounds, streaming, expiry, and terminal publication races. Test-only build authority is explicitly controlled; these cases do not establish a live Iron Bank build or an approved customer staging registry. Actual Registry1 access and Linux runtime qualification remain required for the canonical demonstration.
