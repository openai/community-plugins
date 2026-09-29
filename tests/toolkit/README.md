# Perform tests

The Perform suite is offline and deterministic. It validates the catalog and explicit skill metadata, inspects the package boundary, and exercises action listing, strict selection, missing-variable failure, and safe rendering from a cold copied package.

Run from the repository root:

```sh
npm run test:toolkit
```

The test uses a disposable copied package and the host Python interpreter. It requires Python 3.6 or newer, does not require credentials, and deletes its temporary fixture after a successful or failed assertion.
