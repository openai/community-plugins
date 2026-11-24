# AI/ML API

Thirteen skills for building integrations with [AI/ML API](https://aimlapi.com):
model discovery, text protocols, images, asynchronous media jobs, speech,
decision models, retries, costs, sandbox testing, webhooks and batches.

AI/ML API maintains this plugin in
[`aimlapi/skills`](https://github.com/aimlapi/skills). This copy is version
**1.0.3**, commit
[`097835b6a408c0d33f7f46f7128539681575317e`](https://github.com/aimlapi/skills/tree/097835b6a408c0d33f7f46f7128539681575317e).
The skills, helpers, agent metadata, manifest and logo are unchanged.
[UPSTREAM.json](UPSTREAM.json) records their SHA-256 hashes. Inclusion in this
marketplace does not imply OpenAI endorsement.

## Install

After adding this repository as a Codex plugin marketplace, install:

```sh
codex plugin add aimlapi@community-plugins
```

Start a new chat after installation. Installing the package does not configure
your model provider, authenticate to AI/ML API, or run a helper script.

## Safe first run

Use this prompt with synthetic input:

```text
Use $aimlapi-models to find text models in the public catalogue and explain
which endpoint each supports. Do not run inference, install software, change
agent configuration, or read credentials.
```

Catalogue reads need network access and Python 3.9+ or curl, but no API key.
For an entirely offline check, run this from the plugin folder:

```sh
python3 skills/aimlapi-models/scripts/models.py --help
python3 skills/aimlapi-batch/scripts/batch.py --help
```

If Python or network access is unavailable, stop and report the prerequisite.
Do not invent catalogue results or substitute a paid inference request.

## Skills

| Skill | Workflow |
| --- | --- |
| [aimlapi-setup](skills/aimlapi-setup/SKILL.md) | Sign in and preview agent provider configuration through the optional AIMLAPI CLI |
| [aimlapi-models](skills/aimlapi-models/SKILL.md) | Read model identifiers, endpoint schemas, prices, metrics and deprecations |
| [aimlapi-chat](skills/aimlapi-chat/SKILL.md) | Write text, streaming, tools, structured output and embedding integrations |
| [aimlapi-protocols](skills/aimlapi-protocols/SKILL.md) | Handle translated Chat Completions, Responses and Messages routes |
| [aimlapi-images](skills/aimlapi-images/SKILL.md) | Generate and edit images using model-specific parameters |
| [aimlapi-async-jobs](skills/aimlapi-async-jobs/SKILL.md) | Submit, poll and download video, music and background response jobs |
| [aimlapi-speech](skills/aimlapi-speech/SKILL.md) | Synthesize speech and transcribe audio |
| [aimlapi-decisions](skills/aimlapi-decisions/SKILL.md) | Use typed decisions, probabilities and routers |
| [aimlapi-errors](skills/aimlapi-errors/SKILL.md) | Handle errors, retries and ambiguous billed requests |
| [aimlapi-costs](skills/aimlapi-costs/SKILL.md) | Read request costs, usage, logs and billing records |
| [aimlapi-sandbox](skills/aimlapi-sandbox/SKILL.md) | Exercise response formats and scripted errors in API sandbox mode |
| [aimlapi-webhooks](skills/aimlapi-webhooks/SKILL.md) | Verify signed notifications and manage webhook delivery |
| [aimlapi-batch](skills/aimlapi-batch/SKILL.md) | Validate JSONL locally and submit resumable batches |

## Permissions and authentication

The manifest declares **Read** and **Write**; marketplace authentication is
**ON_USE**. Those labels do not grant an API key. Most helpers read
`AIMLAPI_API_KEY` from the invoking process. Set it outside the chat with your
shell or secret manager; do not paste, print, commit or pass a key as a command
argument. The catalogue and decision-schema readers need no key. Webhook
management requires a management key; receivers use `AIMLAPI_WEBHOOK_SECRET`.

There is no bundled MCP server, app connection, install hook or lifecycle hook.
Python helpers use the standard library; the JavaScript webhook verifier uses
Node built-ins (Node 18+). Code examples may require the SDK or web framework
they name; those dependencies are not installed by this package.

`aimlapi-setup` is an optional, separate workflow. It can fetch the `aimlapi`
CLI from the public npm registry, open browser sign-in, store credentials
through that CLI, and change agent configuration and backups. It requires
Node 18+ and npm. Review the exact CLI version before executing it: the
upstream instructions use `npx -y aimlapi@latest`. The skill requires a dry run
and explicit approval before applying configuration changes, and forbids
reading the stored credential. This marketplace entry does not perform setup.

## Data boundary

| Destination or file | Data or behavior |
| --- | --- |
| `api.aimlapi.com` | Catalogue and schema queries without a key; authorized inference, uploads, usage, billing, batch and webhook operations with a key |
| `docs.aimlapi.com` | Public documentation reads |
| `aimlapi.com` | Dashboard, API key management and optional CLI browser sign-in |
| Public npm registry | Optional `aimlapi` CLI download during setup; no API key is needed for the download |
| Catalogue-provided `docsJson` URLs | Schema readers fetch the URL returned by the catalogue without an API key; inspect the destination before use |
| API-returned result or upload URLs, including `cdn.aimlapi.com` | Generated media, batch result downloads and multipart upload bytes; the helpers do not attach the API key to these requests |
| Your public webhook receiver | AI/ML API sends signed job events and results to the URL specified by your request |
| User-selected local paths | Helpers read request JSON/JSONL and audio inputs, and write requested media, transcripts and batch results |
| `<input>.aimlapi-batch.json` | Batch content hash, uploaded file ID and batch ID for resume; an adjacent `.tmp` is used for atomic writes |
| Agent configuration and CLI credential files | Read or changed only by the optional setup CLI workflow, with its preview and approval steps |

Prompts, selected files and media sent for inference leave the machine and may
be processed by AI/ML API's model providers. Choose inputs you are authorized
to send and review the [privacy policy](https://aimlapi.com/privacy-policy).
Inspect API-returned URLs before fetching sensitive files. HTTP helpers attach
a named `aimlapi-skills/1.0.3` client identifier. Network calls and downloads
happen when a workflow is invoked, not at package installation.

## Failure behavior and costs

- Missing keys stop authenticated helpers before a request. A public catalogue
  query may still run without a key.
- Validate request bodies against the current model schema. Model availability
  and prices can change independently of this pinned package.
- Use sandbox mode for supported inference tests. It still contacts AI/ML API
  and requires a key; it is different from the offline tests below. Sandbox
  does not cover file-based batches, webhooks or all protocol translations.
- Live inference and batch operations can incur charges. Read the selected
  model's current price and obtain the user's authorization before spending.
- Preserve job and batch IDs after timeouts. Resume polling instead of creating
  another billed job. The speech and async helpers distinguish a completed job
  whose download failed from a failed generation.
- Verify webhook signatures and timestamps before trusting events. Keep signing
  secrets in a secret store; never display creation responses in chat.

## Verification and limitations

From the repository root, run:

```sh
npm run validate:aimlapi
npm run test:aimlapi
```

The master suite checks provenance and links, copies the installable folder to
an isolated location, runs CLI checks there, and exercises upstream Python and
JavaScript tests, including loopback HTTP tests. It does not need an API key,
contact the provider or invoke the optional setup CLI. See the
[test guide](../../tests/aimlapi/README.md) for coverage and remaining manual
qualification. Passing these tests does not verify a live provider account,
paid generation, browser sign-in or installed Codex skill selection.

## License and updates

The vendored package is licensed under [Apache-2.0](LICENSE). See
[THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt) for attribution and test-vector
provenance. The license does not grant trademark rights.

Updates are reviewed snapshots, not automatic synchronization with upstream.
Compare the new upstream commit, update the receipt and mapped test fixtures,
review authentication and data flows, and rerun the complete master suite.
