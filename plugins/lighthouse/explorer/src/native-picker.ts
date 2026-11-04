import { isAbsolute } from "node:path";
import { fileURLToPath } from "node:url";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

const extension = "openai/elicitation";
const pickerCapabilities = z.object({
  extensions: z.object({ [extension]: z.object({ form: z.object({}) }) }),
});
const pickerResult = z.discriminatedUnion("action", [
  z.object({ action: z.literal("accept"), content: z.object({ directory: z.string() }) }),
  z.object({ action: z.enum(["cancel", "decline"]) }),
]);

export async function requestBenchmarkDirectory(server: McpServer): Promise<string | null> {
  if (!pickerCapabilities.safeParse(server.server.getClientCapabilities()).success) {
    throw new Error("This version of Codex does not support the native folder picker. Use the folder path option in Lighthouse instead.");
  }

  const response = await server.server.request({
    method: "openai/elicitation/create",
    params: {
      mode: "form",
      message: "Choose a Lighthouse benchmark folder",
      requestedSchema: {
        type: "object",
        properties: {
          directory: {
            type: "string",
            title: "Benchmark folder",
            format: "uri",
            "x-openai-input": { type: "file", options: [], userOptions: { kind: "directory" } },
          },
        },
        required: ["directory"],
      },
    },
  }, pickerResult);

  if (response.action !== "accept") return null;

  try {
    const { directory } = response.content;
    if (!directory.startsWith("file://")) throw new Error();
    const url = new URL(directory);
    if (url.search || url.hash) throw new Error();
    const path = fileURLToPath(url);
    if (!isAbsolute(path) || path.includes("\0")) throw new Error();
    return path;
  } catch {
    throw new Error("Choose a local benchmark folder from this device.");
  }
}
