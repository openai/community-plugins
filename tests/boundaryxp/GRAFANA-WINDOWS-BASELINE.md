# Grafana Windows baseline comparison

2026-11-16, Windows x64, Node 24.19.0. This investigation distinguishes a
BoundaryXP regression from failures already reproducible without BoundaryXP.
It does not modify Grafana, waive its security checks, or claim its Windows
support is complete.

## Controlled comparison

| Snapshot | Commit | Passed | Failed | Skipped |
|---|---|---:|---:|---:|
| Upstream, no BoundaryXP plugin | `6ab9f2066b527e6bc295f638c5b74553a4bf5e05` | 16 | 10 | 6 |
| Initial BoundaryXP contribution | `b66b5a1ba796bc751ebf8ad51faa0184ac8e73f6` | 16 | 10 | 6 |

Both were fresh detached worktrees checked out with `core.autocrlf=false` and
`core.eol=lf`. The same Node executable ran the same Grafana test command.
The 124 Grafana plugin, test, vendored-source and validation files were compared
by SHA-256: **zero differences**. All ten failing test names match exactly.
The PR changes no Grafana implementation, fixtures, tests or source receipts.

Machine-readable counts, names and mechanisms are in
[evidence/grafana-windows-20261116.json](evidence/grafana-windows-20261116.json).
The accompanying TAP logs redact local machine paths only; results are retained.

## Located causes

1. **Entrypoint URL comparison: three failures.** The assembled server ends with
   `pathToFileURL(__filename).href === "file://".concat(process.argv[1])`.
   A Windows absolute path produces different strings: `file:///C:/…` versus
   `file://C:\…`. The guard is false, `main()` is not called, and the process
   closes with code 0. The two stdio tests fail and the startup-denial test does
   not obtain its expected code 1. Source: `mcp/server.mjs.source.part-013`,
   immediately before the final export annotation.
2. **POSIX permission assumptions: six failures.** `assertPrivateDirectory`
   requires owner-only read/search bits; enrollment output also requires no
   group/other bits. Windows `mkdir/chmod(0700)` in the isolated probe reported
   mode `0666`. The trust-root CLI's public error is
   `AUTHORITY_OPERATION_REJECTED`. A disposable assembled diagnostic, changing
   only the caught-error logging to include `error.code`, confirmed the underlying
   **`UNSAFE_MODE`**. No permission checks were disabled. Five synthetic-provider
   tests fail during trust-root setup; the valid enrollment test fails the
   analogous output-mode check. Source: `grafana-authorityctl.mjs.source.part-008`
   (`assertPrivateDirectory`, `writeNoClobber`).
3. **Git line-ending conversion: one remaining source test.** The reverse-patch
   test invokes `git apply` in a newly extracted directory, which still inherits
   this machine's system `core.autocrlf=true` even when the checkout uses LF.
   Limiting `core.autocrlf=false` and `core.eol=lf` to that test's child environment
   made all **four source-provenance tests pass**, without editing product files.

The earlier partially normalized checkout reported 13 failures. Its CRLF
qualification patch had 69,025 bytes instead of the pinned 67,671, and Markdown
examples were parsed using an LF-only fence expression. The exact-byte worktrees
remove those checkout artifacts and are the controlled 10-failure comparison.
This correction supersedes the earlier 13-failure count for diagnosis purposes.

## Reproduce locally

From a clone containing both commits, choose two new empty worktree paths:

```text
git -c core.autocrlf=false -c core.eol=lf worktree add --detach <baseline> 6ab9f2066b527e6bc295f638c5b74553a4bf5e05
git -c core.autocrlf=false -c core.eol=lf worktree add --detach <candidate> b66b5a1ba796bc751ebf8ad51faa0184ac8e73f6
```

In each worktree on Windows x64, Node 24.19:

```text
node --test --test-reporter=tap --test-concurrency=1 tests/grafana-observability/*.test.mjs
```

For the source-provenance diagnostic only, run in a disposable PowerShell
process so these Git overrides are scoped to the test and its children:

```powershell
$env:GIT_CONFIG_COUNT='2'
$env:GIT_CONFIG_KEY_0='core.autocrlf'
$env:GIT_CONFIG_VALUE_0='false'
$env:GIT_CONFIG_KEY_1='core.eol'
$env:GIT_CONFIG_VALUE_1='lf'
node --test tests/grafana-observability/source-provenance.test.mjs
```

No change to global Git settings is required. Do not disable trust-root checks
or treat a skipped macOS/Docker/provider check as a pass. The repository's
marketplace plugin-suite workflow uses macOS; this Windows comparison does not
replace that qualification. Maintainers decide upstream fixes or platform scope.

## Impact on this PR

These observations establish that the reported Windows Grafana failures are
reproducible in the baseline and are not introduced by the BoundaryXP addition.
Grafana is not a dependency of BoundaryXP's locked npm runtime or launcher;
customers do not install or configure it to use BoundaryXP.
They do **not** establish a green full-marketplace run. BoundaryXP's own real
native-package tests and installed-Codex checks remain separately reported.
Original BoundaryXP Core source, existing installations and user data are not
part of this evidence.
