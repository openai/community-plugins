import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(fileURLToPath(new URL("../../..", import.meta.url)));
const readme = fs.readFileSync(
  path.join(repoRoot, "plugins", "ponder", "README.md"),
  "utf8",
);

test("README leads with the safe read-only first run", () => {
  assert.match(readme, /^## Safe first run$/m);
  assert.match(
    readme,
    /Use \$ponder on the bundled synthetic fixture only to inspect unit coverage, pass convergence, and ledger integrity\. Do not edit files yet\./,
  );
  assert.match(readme, /read-only/i);
});

test("README explains permissions, authentication, data boundary, accessibility, and limitations", () => {
  for (const heading of [
    "## Permissions and authentication",
    "## Data boundary",
    "## Accessibility",
    "## Limitations",
  ]) {
    assert.match(readme, new RegExp("^" + heading + "$", "m"));
  }

  assert.match(readme, /\bRead\b/);
  assert.match(readme, /\bWrite\b/);
  assert.match(readme, /ON_USE/);
  assert.match(readme, /explicitly declared session directory/i);
  assert.match(readme, /VoiceOver|Dynamic Type|screen-reader/i);
  assert.match(readme, /does not automatically rewrite|does not substitute/i);
});
