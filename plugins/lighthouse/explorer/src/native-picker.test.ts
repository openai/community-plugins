import assert from "node:assert/strict";
import test from "node:test";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { pathToFileURL } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { RequestSchema, type ClientCapabilities } from "@modelcontextprotocol/sdk/types.js";
import { z } from "zod";
import { requestBenchmarkDirectory } from "./native-picker.ts";

const directoryRequest = RequestSchema.extend({
  method: z.literal("openai/elicitation/create"),
  params: z.object({
    mode: z.literal("form"), message: z.string(), requestedSchema: z.record(z.string(), z.unknown()),
  }),
});
const capabilities: ClientCapabilities = { extensions: { "openai/elicitation": { form: {} } } };

async function withHost(
  advertised: ClientCapabilities,
  respond: (request: z.output<typeof directoryRequest>) => Record<string, unknown>,
  run: (server: McpServer, client: Client) => Promise<void>,
) {
  const server = new McpServer({ name: "test-server", version: "1" });
  const client = new Client({ name: "test-host", version: "1" }, { capabilities: advertised });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  client.setRequestHandler(directoryRequest, respond);
  server.registerTool("choose", {}, async () => ({
    content: [], structuredContent: { directory: await requestBenchmarkDirectory(server) },
  }));
  try {
    await Promise.all([server.connect(serverTransport), client.connect(clientTransport)]);
    await run(server, client);
  } finally {
    await Promise.all([server.close(), client.close()]);
  }
}

test("a native directory request round-trips while an MCP tool is running", async () => {
  const directory = join(tmpdir(), "Lighthouse space # and %");
  await withHost(capabilities, (request) => {
    assert.deepEqual(request.params, {
      mode: "form",
      message: "Choose a Lighthouse benchmark folder",
      requestedSchema: {
        type: "object",
        properties: {
          directory: {
            type: "string", title: "Benchmark folder", format: "uri",
            "x-openai-input": { type: "file", options: [], userOptions: { kind: "directory" } },
          },
        },
        required: ["directory"],
      },
    });
    return { action: "accept", content: { directory: pathToFileURL(directory).href } };
  }, async (_server, client) => {
    const result = await client.callTool({ name: "choose" });
    assert.deepEqual(result.structuredContent, { directory });
  });
});

test("cancel and decline do not select a directory", async () => {
  let action = "cancel";
  await withHost(capabilities, () => ({ action }), async (server) => {
    assert.equal(await requestBenchmarkDirectory(server), null);
    action = "decline";
    assert.equal(await requestBenchmarkDirectory(server), null);
  });
});

test("the picker rejects nonlocal, malformed and decorated accepted URIs", async () => {
  let directory = "";
  await withHost(capabilities, () => ({ action: "accept", content: { directory } }), async (server) => {
    for (const invalid of ["https://example.com/bench", "/tmp/local", "file:relative", "file://remote.example/bench", "file:///tmp/bench?x", "file:///tmp/bench#x", "file:///tmp/%00bench"]) {
      directory = invalid;
      await assert.rejects(requestBenchmarkDirectory(server), /Choose a local benchmark folder/);
    }
  });
});

test("an unavailable or legacy-only native picker returns guidance without sending a request", async () => {
  let requests = 0;
  for (const advertised of [{}, { extensions: { "openai/form": {} } }, { extensions: { "openai/elicitation": {} } }]) {
    await withHost(advertised, () => { requests += 1; return {}; }, async (server) => {
      await assert.rejects(requestBenchmarkDirectory(server), /does not support the native folder picker/);
    });
  }
  assert.equal(requests, 0);
});
