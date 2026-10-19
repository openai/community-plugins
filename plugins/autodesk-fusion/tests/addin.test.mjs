import assert from "node:assert/strict";
import test from "node:test";
import { spawn, execFile } from "node:child_process";
import { createHmac, randomBytes } from "node:crypto";
import { chmod, mkdtemp, readFile, realpath, rm, symlink, writeFile } from "node:fs/promises";
import { request as httpRequest } from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const { AddinDesktopProvider, addinLocalFetch } = await import(process.env.FUSION_ADDIN_TEST_ENTRY ?? "../dist/index.mjs");
const plugin = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const execFileAsync = promisify(execFile);

function desktopRequest(id = "addin-request", args = {}, operation = "parameters.modify") {
  return { operation, args, request_id: id, document_id: "fixture:document", expected_state: "fixture-before" };
}

async function fixture(t, environment = {}) {
  const directory = await mkdtemp(path.join(await realpath(os.tmpdir()), "fusion-addin-test-"));
  const tokenFile = path.join(directory, "private", "session.json");
  let child;
  let stderr = "";
  let stdout = "";
  const messages = [];
  let readyResolve;
  let readyReject;
  const ready = new Promise((resolve, reject) => { readyResolve = resolve; readyReject = reject; });
  child = spawn(process.env.FUSION_TEST_PYTHON ?? (process.platform === 'win32' ? 'python' : 'python3'), [path.join(plugin, "addin", "tests", "protocol_fixture.py")], {
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", FUSION_ADDIN_FIXTURE_TOKEN_FILE: tokenFile, ...environment },
    stdio: ["ignore", "pipe", "pipe"],
  });
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", chunk => {
    stdout += chunk;
    let end;
    while ((end = stdout.indexOf("\n")) !== -1) {
      const line = stdout.slice(0, end);
      stdout = stdout.slice(end + 1);
      try { const parsed = JSON.parse(line); messages.push(parsed); child.emit("fixture-message", parsed); if (parsed.url) readyResolve(parsed); }
      catch { readyReject(new Error("Add-in fixture emitted invalid startup data")); }
    }
  });
  child.stderr.on("data", chunk => { stderr = (stderr + chunk).slice(0, 8000); });
  child.on("error", readyReject);
  const exited = new Promise(resolve => child.once("exit", (status, signal) => { readyReject(new Error(`Add-in fixture exited (${status ?? signal}): ${stderr.slice(0, 500)}`)); resolve(); }));
  const startupTimer = setTimeout(() => readyReject(new Error(`Add-in fixture did not start: ${stderr.slice(0, 500)}`)), 5000);
  t.after(async () => {
    clearTimeout(startupTimer);
    if (child.exitCode === null && child.signalCode === null) child.kill("SIGTERM");
    let timer;
    await Promise.race([exited, new Promise(resolve => { timer = setTimeout(() => { child.kill("SIGKILL"); resolve(); }, 2000); })]);
    clearTimeout(timer);
    await rm(directory, { recursive: true, force: true });
  });
  const info = await ready;
  clearTimeout(startupTimer);
  return { ...info, tokenFile, directory, child, messages };
}

function waitForFixtureCompletion(running, requestId) {
  const completed = running.messages.find(message => message.completedRequestId === requestId);
  if (completed) return Promise.resolve(completed);
  assert.equal(running.child.exitCode, null, "Add-in fixture exited before completing the request");
  assert.equal(running.child.signalCode, null, "Add-in fixture stopped before completing the request");
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      clearTimeout(timer);
      running.child.off("fixture-message", onMessage);
      running.child.off("exit", onExit);
    };
    const onMessage = message => {
      if (message.completedRequestId !== requestId) return;
      cleanup();
      resolve(message);
    };
    const onExit = () => {
      cleanup();
      reject(new Error(`Add-in fixture exited before completing ${requestId}`));
    };
    const timer = setTimeout(() => {
      cleanup();
      reject(new Error(`Add-in fixture did not complete ${requestId} within 5 seconds`));
    }, 5000);
    running.child.on("fixture-message", onMessage);
    running.child.once("exit", onExit);
  });
}

function provider(t, running, overrides = {}) {
  const client = new AddinDesktopProvider({ url: running.url, tokenFile: running.tokenFile, handlerHash: running.handlerHash, timeoutMs: 2000, ...overrides });
  t.after(() => client.close());
  return client;
}

async function credentials(running) { return JSON.parse(await readFile(running.tokenFile, "utf8")); }
function signature(secret, prefix, body) { return createHmac("sha256", Buffer.from(secret, "hex")).update(prefix).update(body).digest("hex"); }

function signedRequest(pairing, request, overrides = {}) {
  const method = request ? "POST" : "GET";
  const body = request ? JSON.stringify({ version: 1, session_id: pairing.session_id, handler_hash: pairing.handler_hash, request }) : "";
  const nonce = overrides.nonce ?? randomBytes(24).toString("hex");
  const timestamp = overrides.timestamp ?? String(Math.floor(Date.now() / 1000));
  const headers = { "Content-Type": "application/json", "X-Codex-Fusion-Session": pairing.session_id,
    "X-Codex-Fusion-Nonce": nonce, "X-Codex-Fusion-Timestamp": timestamp,
    "X-Codex-Fusion-Signature": signature(pairing.token, `request\n${method}\n/dispatch\n${pairing.session_id}\n${nonce}\n${timestamp}\n`, body),
    ...overrides.headers };
  return { method, headers, ...(request ? { body } : {}) };
}

async function rawHttp(url, { headers, body = "", method = "POST" }) {
  return new Promise((resolve, reject) => {
    const outgoing = httpRequest(url, { method, headers }, incoming => {
      const chunks = [];
      incoming.on("data", chunk => chunks.push(chunk));
      incoming.on("end", () => resolve({ status: incoming.statusCode, body: Buffer.concat(chunks).toString("utf8") }));
    });
    outgoing.on("error", reject);
    outgoing.end(body);
  });
}

test("optional add-in Python queue, main-thread, package and lifecycle doubles pass", async () => {
  const { stderr } = await execFileAsync(process.env.FUSION_TEST_PYTHON ?? (process.platform === 'win32' ? 'python' : 'python3'), [path.join(plugin, "addin", "tests", "test_bridge.py")], { timeout: 20_000, maxBuffer: 64_000, env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
  assert.match(stderr, /Ran 12 tests/u);
  assert.match(stderr, /OK/u);
});

test("real Python add-in bridge authenticates status and dispatch without transmitting its secret", async t => {
  const running = await fixture(t);
  const observed = [];
  const client = provider(t, running, { fetchImpl: async (input, init) => { observed.push({ url: String(input), headers: init.headers, body: init.body }); return addinLocalFetch(input, init); } });
  const connection = await client.connect();
  assert.equal(connection.authenticated, true);
  assert.equal(connection.trustBoundary, "same_os_user");
  assert.equal(connection.liveQualification, "not_inferred");
  assert.equal(connection.handlerHash, running.handlerHash);
  const args = { expression: "25.4 mm", malicious_name: "'); __import__('os').system('injected'); # 中文🔩" };
  const result = await client.dispatch(desktopRequest("real-protocol", args));
  assert.equal(result.ok, true);
  assert.equal(result.data.thread, "main");
  assert.deepEqual(result.data.args, args);
  assert.equal(result.data.calls, 1);
  const pairing = await credentials(running);
  assert.equal(JSON.stringify(observed).includes(pairing.token), false);
  assert.equal(JSON.stringify(connection).includes(pairing.token), false);
  assert.equal(observed.filter(item => item.body).length, 1);
});

test("add-in endpoint and private pairing file cannot be redirected by profile values", async t => {
  const running = await fixture(t);
  for (const url of ["http://localhost:27183/dispatch", "http://127.0.0.1:27183/mcp", "https://127.0.0.1:27183/dispatch", "http://127.0.0.1:27183/dispatch?token=secret"]) {
    assert.throws(() => new AddinDesktopProvider({ url, tokenFile: running.tokenFile, handlerHash: running.handlerHash }));
  }
  assert.throws(() => new AddinDesktopProvider({ url: running.url, tokenFile: "relative.json", handlerHash: running.handlerHash }));
  assert.throws(() => new AddinDesktopProvider({ url: running.url, tokenFile: running.tokenFile, handlerHash: "unverified" }));
  let networkCalls = 0;
  const client = provider(t, running, { handlerHash: "e".repeat(64), fetchImpl: async () => { networkCalls++; throw new Error("should not send"); } });
  const result = await client.dispatch(desktopRequest());
  assert.equal(result.error.code, "PAIRING_MISMATCH");
  assert.equal(result.error.outcome, "none");
  assert.equal(networkCalls, 0);
});

test("private pairing rejects shared files and shared directories before any HTTP call", { skip: process.platform === "win32" ? "POSIX permission-bit case; Windows uses explicit ACL checks" : false }, async t => {
  const running = await fixture(t);
  let calls = 0;
  const client = provider(t, running, { fetchImpl: async () => { calls++; throw new Error("unexpected network"); } });
  await chmod(running.tokenFile, 0o644);
  assert.equal((await client.dispatch(desktopRequest())).error.code, "UNSAFE_PAIRING_FILE");
  await chmod(running.tokenFile, 0o600);
  await chmod(path.dirname(running.tokenFile), 0o755);
  assert.equal((await client.dispatch(desktopRequest())).error.code, "UNSAFE_PAIRING_FILE");
  await chmod(path.dirname(running.tokenFile), 0o700);
  assert.equal(calls, 0);
});

test("private pairing rejects symbolic files and linked ancestor directories", { skip: process.platform === "win32" ? "Windows symlink creation requires an independently configured privilege" : false }, async t => {
  const running = await fixture(t);
  const linkedFile = path.join(path.dirname(running.tokenFile), "linked.json");
  await symlink(running.tokenFile, linkedFile);
  assert.equal((await provider(t, running, { tokenFile: linkedFile }).dispatch(desktopRequest())).error.code, "UNSAFE_PAIRING_FILE");
  const linkedDirectory = path.join(running.directory, "linked-parent");
  await symlink(path.dirname(running.tokenFile), linkedDirectory);
  assert.equal((await provider(t, running, { tokenFile: path.join(linkedDirectory, "session.json") }).dispatch(desktopRequest())).error.code, "UNSAFE_PAIRING_FILE");
});

test("malformed, oversized and mismatched private pairing records fail without leaking their contents", async t => {
  const running = await fixture(t);
  const original = await readFile(running.tokenFile, "utf8");
  const client = provider(t, running);
  for (const bad of ["x".repeat(9000), "not JSON SECRET_CANARY", JSON.stringify({ ...JSON.parse(original), url: "http://127.0.0.1:49999/dispatch", token: "SECRET_CANARY" })]) {
    await writeFile(running.tokenFile, bad, { mode: 0o600 });
    const result = await client.dispatch(desktopRequest());
    assert.equal(result.ok, false);
    assert.equal(result.error.outcome, "none");
    assert.equal(JSON.stringify(result).includes("SECRET_CANARY"), false);
  }
  await writeFile(running.tokenFile, original, { mode: 0o600 });
});

test("an unsigned or spoofed listener never receives a desktop request or bearer secret", async t => {
  const running = await fixture(t);
  const requests = [];
  const client = provider(t, running, { fetchImpl: async (_input, init) => { requests.push(init); return new Response(JSON.stringify({ ok: true }), { headers: { "content-type": "application/json" } }); } });
  const result = await client.dispatch(desktopRequest("untrusted-listener", { private_dimension: "18 mm" }));
  assert.equal(result.error.code, "AUTHENTICATION_FAILED");
  assert.equal(result.error.outcome, "none");
  assert.equal(requests.length, 1);
  assert.equal(requests[0].method, "GET");
  assert.equal(requests[0].body, undefined);
  assert.equal(JSON.stringify(requests).includes((await credentials(running)).token), false);
});

test("signed response tampering or identity substitution cannot certify a mutation", async t => {
  for (const validSignature of [false, true]) {
    const running = await fixture(t);
    const pairing = await credentials(running);
    let posts = 0;
    const client = provider(t, running, { fetchImpl: async (input, init) => {
      const response = await addinLocalFetch(input, init);
      if (init.method !== "POST") return response;
      posts++;
      const decoded = JSON.parse(await response.text());
      decoded.request_id = "different-request";
      const body = JSON.stringify(decoded);
      const headers = new Headers(response.headers);
      headers.delete("content-length");
      if (validSignature) headers.set("X-Codex-Fusion-Signature", signature(pairing.token, `response\n${init.headers["X-Codex-Fusion-Nonce"]}\n${response.status}\n`, body));
      return new Response(body, { status: response.status, headers });
    } });
    const result = await client.dispatch(desktopRequest());
    assert.equal(result.error.code, "OUTCOME_UNKNOWN");
    assert.equal(result.error.outcome, "unknown");
    assert.equal(posts, 1);
  }
});

test("add-in refuses browser requests, Host substitution, stale authentication and request replay", async t => {
  const running = await fixture(t);
  const pairing = await credentials(running);
  for (const headers of [{ Origin: "https://attacker.example" }, { "Sec-Fetch-Site": "cross-site" }, { Host: "attacker.example" }]) {
    const result = await addinLocalFetch(running.url, signedRequest(pairing, undefined, { headers }));
    assert.equal(result.status, 403);
  }
  const stale = await addinLocalFetch(running.url, signedRequest(pairing, undefined, { timestamp: String(Math.floor(Date.now() / 1000) - 90) }));
  assert.equal(stale.status, 401);
  const signed = signedRequest(pairing, desktopRequest("replay-test"));
  const first = await addinLocalFetch(running.url, signed);
  assert.equal((await first.json()).response.data.calls, 1);
  const duplicate = await addinLocalFetch(running.url, signed);
  assert.equal(duplicate.status, 409);
  const repeatedId = await addinLocalFetch(running.url, signedRequest(pairing, desktopRequest("replay-test")));
  assert.equal((await repeatedId.json()).response.data.calls, 1);
  const changedId = await addinLocalFetch(running.url, signedRequest(pairing, desktopRequest("replay-test", { changed: true })));
  assert.equal((await changedId.json()).response.error.code, "IDEMPOTENCY_CONFLICT");
});

test("add-in rejects chunked requests and duplicate authentication headers", async t => {
  const running = await fixture(t);
  const pairing = await credentials(running);
  const signed = signedRequest(pairing, desktopRequest("bad-http"));
  const chunked = await rawHttp(running.url, { method: "POST", headers: { ...signed.headers, "Transfer-Encoding": "chunked" }, body: signed.body });
  assert.equal(chunked.status, 415);
  const rawHeaders = ["Host", new URL(running.url).host, "Content-Length", String(Buffer.byteLength(signed.body))];
  for (const [key, value] of Object.entries(signed.headers)) rawHeaders.push(key, value);
  rawHeaders.push("X-Codex-Fusion-Nonce", "a".repeat(48));
  const duplicate = await rawHttp(running.url, { method: "POST", headers: rawHeaders, body: signed.body });
  assert.equal(duplicate.status, 401);
});

test("invalid typed add-in payload is authenticated but rejected before main-thread dispatch", async t => {
  const running = await fixture(t);
  const pairing = await credentials(running);
  for (const badRequest of [
    { ...desktopRequest(), code: "unapproved Python" },
    { ...desktopRequest(), operation: "exec(source)" },
    { ...desktopRequest(), args: ["untyped"] },
  ]) {
    const response = await addinLocalFetch(running.url, signedRequest(pairing, badRequest));
    assert.equal(response.status, 400);
    assert.equal((await response.json()).response.error.outcome, "none");
  }
  assert.equal((await provider(t, running).dispatch(desktopRequest("first-valid"))).data.calls, 1);
});

test("queued add-in deadline expires with no late desktop execution", async t => {
  const running = await fixture(t, { FUSION_ADDIN_FIXTURE_MODE: "no_events", FUSION_ADDIN_FIXTURE_TIMEOUT: "0.1" });
  const result = await provider(t, running).dispatch(desktopRequest());
  assert.equal(result.error.code, "ADDIN_TIMEOUT");
  assert.equal(result.error.outcome, "none");
});

test("running add-in timeout stays unknown and the retained request reconciles without re-execution", async t => {
  const running = await fixture(t, { FUSION_ADDIN_FIXTURE_TIMEOUT: "0.1" });
  let posts = 0;
  const client = provider(t, running, { fetchImpl: async (input, init) => { if (init.method === "POST") posts++; return addinLocalFetch(input, init); } });
  const request = desktopRequest("slow-request", { delay: 0.3 });
  const result = await client.dispatch(request);
  assert.equal(result.error.code, "OUTCOME_UNKNOWN");
  assert.equal(result.error.outcome, "unknown");
  assert.equal(posts, 1);
  // Observe cached completion through the fixture, without retrying the mutation
  // or assuming Python and Node will be scheduled within a fixed sleep window.
  const completed = await waitForFixtureCompletion(running, request.request_id);
  assert.equal(completed.calls, 1);
  const reconciled = await client.dispatch(request);
  assert.equal(reconciled.ok, true);
  assert.equal(reconciled.data.calls, 1);
  assert.equal(posts, 2, "Only the original request and explicit reconciliation may be sent");
});

test("client deadline and close never automatically retry an add-in mutation", async t => {
  const running = await fixture(t);
  let posts = 0;
  const client = provider(t, running, { timeoutMs: 100, fetchImpl: async (input, init) => { if (init.method === "POST") posts++; return addinLocalFetch(input, init); } });
  const result = await client.dispatch(desktopRequest("client-timeout", { delay: 0.4 }));
  assert.equal(result.error.code, "OUTCOME_UNKNOWN");
  assert.equal(posts, 1);
  await client.close();
  const closed = await client.dispatch(desktopRequest("after-close"));
  assert.equal(closed.error.code, "NATIVE_CANCELLED");
  assert.equal(closed.error.outcome, "none");
  assert.equal(posts, 1);
});

test("known handler partial effects and uncertainty about prior jobs remain explicit", async t => {
  const running = await fixture(t);
  const client = provider(t, running);
  assert.equal((await client.dispatch(desktopRequest("before-write", { failure: "none" }))).error.outcome, "none");
  const partial = await client.dispatch(desktopRequest("partial", { failure: "partial" }));
  assert.equal(partial.error.code, "FIXTURE_REJECTION");
  assert.equal(partial.error.outcome, "partial");
  const unknown = await client.dispatch(desktopRequest("unknown", { failure: "unknown" }));
  assert.equal(unknown.error.code, "OUTCOME_UNKNOWN");
  const read = await client.dispatch(desktopRequest("read-failure", { failure: "unknown" }, "document.inspect"));
  assert.equal(read.error.code, "FIXTURE_REJECTION");
  assert.equal(read.error.outcome, "unknown");
});

test("a catalog-valid one-MiB tool JSON value fits the signed desktop request envelope", async t => {
  const running = await fixture(t);
  const client = provider(t, running);
  const toolJson = "x".repeat(1024 * 1024);
  const result = await client.dispatch(desktopRequest("large-tool", { tool_json: toolJson }));
  assert.equal(result.ok, true);
  assert.equal(result.data.args.tool_json.length, toolJson.length);
});

test("HTTP redirect on add-in status is refused before arguments are submitted", async t => {
  const running = await fixture(t);
  const methods = [];
  const client = provider(t, running, { fetchImpl: async (_input, init) => { methods.push(init.method); return new Response(null, { status: 307, headers: { location: "http://127.0.0.1:49999/dispatch" } }); } });
  const result = await client.dispatch(desktopRequest());
  assert.equal(result.error.code, "REDIRECT_REFUSED");
  assert.equal(result.error.outcome, "none");
  assert.deepEqual(methods, ["GET"]);
});

test("oversized signed add-in response is bounded before parsing and never certifies a write", async t => {
  const running = await fixture(t);
  let posts = 0;
  const client = provider(t, running, { maxResponseBytes: 1024, fetchImpl: async (input, init) => {
    if (init.method === "POST") { posts++; return new Response("x".repeat(4096), { headers: { "content-type": "application/json" } }); }
    return addinLocalFetch(input, init);
  } });
  const result = await client.dispatch(desktopRequest());
  assert.equal(result.error.code, "OUTCOME_UNKNOWN");
  assert.equal(posts, 1);
});
