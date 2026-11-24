# AI/ML API tests

Run `npm run test:aimlapi` from the repository root with Node 22.19+ and
Python 3.9+. Set `AIMLAPI_TEST_PYTHON` to a Python executable if `python3` is not
the correct interpreter. No third-party test dependencies are needed.

The single master command runs:

1. Package validation: source hashes, skill frontmatter and local references,
   icon paths and the absence of unbundled MCP/app/hook declarations.
2. Unit and security tests ported from `aimlapi/skills` commit
   `097835b6a408c0d33f7f46f7128539681575317e`: schema alternatives, batch file
   validation and resume/idempotency behavior, response parsing, webhook
   signatures, changed bodies, timestamp skew and missing secrets.
3. Loopback HTTP tests: async/speech polling, multipart audio, binary audio,
   transient failures, API redirect refusal, credential-free batch and media
   downloads. Servers bind only to loopback; provider calls are not made.
4. A mocked sandbox request test: verifies the sandbox header and request body
   without making an HTTP request.
5. A cold copied-package test: discovers all skills, runs helper CLI entrypoints
   from an unrelated working directory, checks valid/invalid JSONL and confirms
   missing credentials stop authenticated helpers before networking.
6. JavaScript webhook verification tests against the published test vector.

The master removes real AIMLAPI credentials and live-test switches from child
process environments. Unit fixtures use synthetic keys only. Source receipts
verify the copied files; they do not replace upstream code review or establish
the trustworthiness of the upstream repository.

The imported unit tests resolve packaged helpers under `plugins/aimlapi` rather
than the source repository's root. `test_scripts.py` additionally imports the
unmodified upstream content checker from `support/`. The JavaScript test's
verifier import is similarly relocated. Original and adapted hashes are in
[support/UPSTREAM.json](support/UPSTREAM.json).

## Manual qualification

These checks are not run by the offline master or claimed as passing:

- Install `aimlapi@community-plugins` into Codex, start a new chat and exercise
  the safe first prompt from the plugin README.
- With a dedicated sandbox key, test supported API response/stream formats.
- With explicit spending authorization, test a small live request for features
  sandbox does not cover, such as file-based batches and protocol translation.
- Inspect and test the optional setup CLI's version, browser sign-in, dry run,
  configuration application, credential storage and rollback on supported OSes.
- Validate delivery to a dedicated public webhook receiver, including rotation.

No paid requests, account changes, provider setup, npm CLI installation or
webhook secret creation is performed by this suite. Maintainers must verify
the contributor agreement and approve the latest changes before merging.
