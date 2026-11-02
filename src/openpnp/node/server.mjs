#!/usr/bin/env node
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { CallToolRequestSchema, ListToolsRequestSchema, ListResourcesRequestSchema, ReadResourceRequestSchema } from '@modelcontextprotocol/sdk/types.js';
import { TOOL_DEFINITIONS, publicDefinition } from './contracts.mjs';
import { OpenPnpRuntime } from './runtime.mjs';
import { publicError } from './errors.mjs';
import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { realpathSync } from 'node:fs';
import { ResponseStore, boundedReply } from './responses.mjs';
import * as domain from './domain/index.mjs';

export function createServer(runtime = new OpenPnpRuntime({ domain }), { responses = runtime.responses ?? new ResponseStore() } = {}) {
  const server = new Server({ name: 'openpnp', version: '0.1.0' }, {
    capabilities: { tools: {}, resources: {} },
    instructions: 'Discover OpenPnP capabilities first. Native bridge ownership and validation govern machine operations. Simulator evidence does not qualify physical hardware. A timeout never proves an action stopped. A truncated response is an explicit summary: use openpnp_read_response_page with its response_retention.response_id for retained details. Root bytes pages reconstruct the exact full response. A details_unavailable storage result does not change the known native outcome and never authorizes replaying an action.',
  });
  server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: TOOL_DEFINITIONS.map(publicDefinition) }));
  server.setRequestHandler(CallToolRequestSchema, async request => {
    try {
      const result = await runtime.call(request.params.name, request.params.arguments ?? {});
      let structuredContent = result && typeof result === 'object' && !Array.isArray(result) ? result : { result };
      let nativeImage;
      if (request.params.name === 'openpnp_get_native_artifact' && typeof result.base64 === 'string') {
        const bytes = Buffer.from(result.base64, 'base64');
        if (bytes.toString('base64') !== result.base64 || createHash('sha256').update(bytes).digest('hex') !== result.sha256) throw new Error('Invalid native artifact bytes');
        if (result.mime_type === 'image/png') {
          if (bytes.subarray(0, 8).toString('hex') !== '89504e470d0a1a0a') throw new Error('Invalid native camera artifact');
          const { base64, ...metadata } = result; structuredContent = metadata;
          nativeImage = { type: 'image', mimeType: 'image/png', data: base64 };
        }
      }
      structuredContent = await boundedReply(structuredContent, responses);
      const content = [{ type: 'text', text: JSON.stringify(structuredContent) }];
      if (nativeImage) content.push(nativeImage);
      return { content, structuredContent };
    } catch (error) {
      const structuredContent = await boundedReply({ error: publicError(error) }, responses);
      return { isError: true, content: [{ type: 'text', text: JSON.stringify(structuredContent) }], structuredContent };
    }
  });
  server.setRequestHandler(ListResourcesRequestSchema, async () => ({ resources: [{ uri: 'openpnp://capabilities', name: 'OpenPnP capabilities', mimeType: 'application/json', description: 'Current native and offline capabilities, simulator attestation, and limitations.' }] }));
  server.setRequestHandler(ReadResourceRequestSchema, async request => {
    if (request.params.uri !== 'openpnp://capabilities') throw new Error('Unknown OpenPnP resource');
    return { contents: [{ uri: request.params.uri, mimeType: 'application/json', text: JSON.stringify(await boundedReply(await runtime.call('openpnp_get_capabilities'), responses)) }] };
  });
  return server;
}

// Node resolves this module through symlinks (including macOS /var -> /private/var).
// Resolve argv's entrypoint too, so copied installs start through the same aliases.
let isMain = false;
try { isMain = Boolean(process.argv[1]) && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href; } catch {}
if (isMain && process.argv.includes('--stdio')) {
  const server = createServer();
  await server.connect(new StdioServerTransport());
  const close = () => { server.close().catch(() => {}).finally(() => process.exit(0)); };
  process.once('SIGINT', close); process.once('SIGTERM', close);
} else if (isMain) {
  console.error('Usage: node server.mjs --stdio');
  process.exitCode = 2;
}
