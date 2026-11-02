// Captured real native simulator receipts; this projection test performs no native actions.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { projectProgress } from '../../src/openpnp/node/progress.mjs';

const native = JSON.parse(await readFile(new URL('./fixtures/native-completion-receipts.json', import.meta.url)));
for (const [mode, observation] of Object.entries(native.observations)) {
  test(`actual native ${mode} preserves unknown and known body in compact polls`, () => {
    const op = observation.operation;
    for (const tool of ['openpnp_get_operation', 'openpnp_get_request_status']) {
      const projected = projectProgress(tool, tool === 'openpnp_get_operation' ? op : { found: true, operation: op });
      const value = tool === 'openpnp_get_operation' ? projected : projected.operation;
      assert.equal(value.state, 'outcome_unknown');
      assert.equal(value.operation_id, op.operation_id);
      assert.equal(value.result.code, op.result.code);
      assert.deepEqual(value.result.known_body_outcome, op.result.known_body_outcome);
      assert.equal(value.native_completion.phase, op.native_completion.phase);
      if (mode.startsWith('cancel')) {
        assert.equal(value.native_completion.ownership_retained, true);
        assert.equal(value.native_completion.completion_observation.native_wrapper_completed, false);
      }
    }
  });
}
test('faulted publication retains last committed state, observation and explicit nondurability', () => {
  const known = { captured: true, state: 'succeeded', result: { enabled: false } };
  const fault = { code: 'NATIVE_COMPLETION_PUBLICATION_FAILED', durable: false, repeat_action_performed: false,
    known_body_outcome: known, completion_observation: { native_wrapper_completed: true, native_wrapper_succeeded: false,
      wrapper_error: { type: 'java.lang.IllegalStateException', message: 'controlled late error' } } };
  const pending = { submission_id: 'bound-generation', operation_id: 'original', phase: 'publication-fault', ownership_retained: true, publication_fault: fault };
  const op = projectProgress('openpnp_get_operation', { operation_id: 'original', state: 'running', native_completion: pending, publication_fault: fault });
  assert.equal(op.state, 'running'); assert.deepEqual(op.publication_fault, fault);
  assert.equal(op.native_completion.ownership_retained, true);
  const status = projectProgress('openpnp_get_status', { native_busy: false, journal_fault: true, native_submission: pending });
  assert.deepEqual(status.native_submission.publication_fault, fault);
  assert.equal(status.native_submission.operation_id, 'original');
});
test('large known job results remain bounded without claiming their details were retained', () => {
  const result = { placed: 3, requested: 10000, independently_inspected: 0, placements: Array.from({length:10000},()=>({placed:false})) };
  const out = projectProgress('openpnp_get_operation', { state:'outcome_unknown', result: { code:'NATIVE_WRAPPER_FAILED', known_body_outcome: {captured:true,state:'succeeded',result} } });
  assert.equal(out.result.known_body_outcome.result.placements_count,10000);
  assert.equal(out.result.known_body_outcome.result.placed,3);
  assert.equal(out.result.known_body_outcome.result.placements,undefined);
  assert.equal(out.response_view.full_details_included,false);
  assert.ok(Buffer.byteLength(JSON.stringify(out))<32768);
});
test('new scalar fields reject object substitution and oversized identifiers', () => {
  for (const native_completion of [{ phase:{} },{ submission_id:'x'.repeat(1025) }]) {
    assert.throws(()=>projectProgress('openpnp_get_operation',{native_completion}),{code:'INVALID_PROGRESS_RESPONSE'});
  }
});
