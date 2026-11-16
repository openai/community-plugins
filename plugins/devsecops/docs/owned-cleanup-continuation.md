# Resident cleanup continuation

Start the existing canonical `e2e --driver codex-cli` command with `--resident-cleanup-controls` when the operator needs to preserve the process for cleanup reconciliation. All original explicit package/configuration/selection inputs remain required. Interactive stdin and stderr terminals are checked before the first effect. This option never enables a permission absent from the selected configuration.

When cleanup has an uncertain outcome, the resident command shows its exact run and operation and a fresh challenge. Enter the displayed `reconcile <operation-id> <challenge>` command to make read-only observations. Unknown or in-flight effects remain unresolved; missing files are not a terminal result.

If all attempted effects are terminal but cleanup is incomplete, the journal records the previous operation as `partial`, keeps the environment reservation and active cleanup phase, and displays a different challenge. Enter the displayed `continue <prior-operation-id> <challenge>` to admit a new down operation. The current explicit cleanup grant must remain unexpired. The new operation durably records `continues_operation`; it uses original resident component handles, skips scopes whose original absence was observed, and reaches never-dispatched lower scopes in the fixed dependency order. A partial component requires its separately implemented fixed continuation protocol. Reconciliation never retries deletion.

The dedicated runner guest has a separate restricted disposal path. Only the
original component validator can establish that remaining uncertainty is
contained within the original owned guest, with parent ownership observed and
the original GitLab runner registration already revoked. The journal seals that
partial observation with only `dispose-owned-runner-guest` admitted. It preserves
the environment reservation and the earlier uncertain guest evidence; it does
not classify those guest effects as successfully reconciled.

For this restricted state, the terminal displays
`dispose-runner-guest <prior-operation-id> <fresh-challenge>`. Entering that exact
command admits a new down operation under the current unexpired cleanup grant.
An ordinary `continue` command is rejected. Host-route removal and original VM
disposal still require their separately checked resident ownership and scope;
there is no automatic disposal, SSH retry, alternate guest command or authority
restored from the recorded mode. A missing containment or revocation proof leaves
the existing cleanup operation unresolved.

Only complete original cleanup observations permit atomic release. The original scenario failure remains recorded; successful cleanup does not turn an interrupted demo into a successful E2E run. Finalization runs after release through the existing lifecycle.

The dedicated runner VM uses the original `owned-bootstrap-v2` selection and the
sealed `owned-runner-vm-v1` inventory contract. Its exact eleven scopes are
`foundation`, `registry`, `services`, `service-links`, `kubernetes`, `writer`,
`reader`, `gitlab`, `runner-service`, `runner-routes` and `runner-vm`. Missing or
substituted scopes cannot satisfy D6. The previous eight-scope container-runner
contract is diagnostic only and cannot qualify a complete canonical replay.

Final acceptance audits the original resident bootstrap selection and the
original creation and cleanup outcome handles for all three runner components.
Their public observations must bind the same run, guest generation, boot ID,
UID/GID 10001, root and disk identities, runtime/network IDs and SSH host-key pin.
The original components audit their retained evidence without reconnecting to a
removed guest or recreating capability from JSON. A successful preboot rollback
can close a cleanup action, but its absent guest identity cannot prove the full
demo ran. Runner process/configuration absence, host-route removal and VM removal
require separate original observations. The fixed guest-disposal path can close
guest-local residue only with its original typed kernel-cessation proof, after
actual registration revocation and host-route absence. VM removal alone or a
saved cleanup report cannot substitute for these component proofs. This cleanup
does not reconcile uncertain CI effects or erase an earlier scenario failure.

The control loop permits at most 16 commands over five minutes. `stop <challenge>`, end of input, or timeout exits with the retained result. A wrong or stale challenge is rejected. It does not read control files, accept piped commands, execute arbitrary callbacks, or recreate authority from saved reports. Process loss still requires the existing supported inspection/recovery route; ordinary `--resume` cannot restore these original objects.

The equivalent resident APIs are `LifecycleEngine.reconcile_cleanup(operation_id=...)` and `LifecycleEngine.continue_cleanup(prior_operation_id=...)`. The restricted disposal state requires the additional explicit `cleanup_mode="dispose-owned-runner-guest"` argument; it is rejected for an ordinary partial cleanup, and omission is rejected for a disposal-only partial state. Original `NativeFactorySession` instances expose the same explicit methods even after native cancellation, because stopped model execution must not prevent inspecting owned cleanup effects. Ordinary `advance()` stops at `cleanup_continuation_required` and does not call either method automatically.
