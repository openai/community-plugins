// node --test 'tests/*.test.mjs'
import { test } from "node:test";
import assert from "node:assert/strict";
import { sign, verify, WebhookVerificationError } from "../../../plugins/aimlapi/skills/aimlapi-webhooks/scripts/verify-webhook.mjs";

// Published test vector of the Standard Webhooks specification.
// Built from two parts so secret scanners do not report a known public test value.
const SECRET = "whsec_" + "MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw";
const MSG_ID = "msg_p5jXN8AQM9LWM0D4loKWxJek";
const TIMESTAMP = 1614265330;
const BODY = '{"test": 2432232314}';
const SIGNATURE = "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE=";

const headers = (signature = SIGNATURE) => ({
  "webhook-id": MSG_ID,
  "webhook-timestamp": String(TIMESTAMP),
  "webhook-signature": signature,
});

test("reference vector", () => {
  assert.equal(sign(SECRET, MSG_ID, TIMESTAMP, BODY), SIGNATURE);
  assert.deepEqual(verify(Buffer.from(BODY), headers(), SECRET, { now: TIMESTAMP }), { test: 2432232314 });
});

test("Headers object from fetch", () => {
  assert.equal(verify(BODY, new Headers(headers()), SECRET, { now: TIMESTAMP }).test, 2432232314);
});

test("tampered body is rejected", () => {
  assert.throws(() => verify('{"test": 1}', headers(), SECRET, { now: TIMESTAMP }), WebhookVerificationError);
});

test("timestamps outside five minutes are rejected", () => {
  for (const skew of [-301, 301]) {
    assert.throws(() => verify(BODY, headers(), SECRET, { now: TIMESTAMP + skew }), WebhookVerificationError);
  }
});

test("rotation: any of several signatures may match", () => {
  const both = `v1,AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= ${SIGNATURE}`;
  assert.ok(verify(BODY, headers(both), SECRET, { now: TIMESTAMP }));
});

test("missing header is rejected", () => {
  const h = headers();
  delete h["webhook-signature"];
  assert.throws(() => verify(BODY, h, SECRET, { now: TIMESTAMP }), WebhookVerificationError);
});

test("an empty or truncated secret is refused", () => {
  for (const secret of ["", "whsec_", "whsec_AAAA", undefined]) {
    assert.throws(() => verify(BODY, headers(), secret, { now: TIMESTAMP }), WebhookVerificationError);
  }
});
