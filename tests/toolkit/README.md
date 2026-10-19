# Perform tests

The Perform suite is offline and deterministic. It validates the catalog and explicit skill metadata, inspects the package boundary, and exercises action listing, strict selection, missing-variable failure, and safe rendering from a cold copied package.

The behavioral integration tests use a cold copied plugin and temporary action directories. They check source precedence, malformed files, invalid overrides, strict selection, and safe catalogue writes without running a configured action or contacting external services.

Run from the repository root:

```sh
npm run test:toolkit
```

The tests use disposable copied packages and the host Python interpreter. They require Python 3.6 or newer, do not require credentials, and delete their temporary fixtures after successful or failed assertions.
