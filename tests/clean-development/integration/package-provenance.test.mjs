import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fixture } from "../support/fixture.mjs";

const expected = {
  "skills/clean-development/SKILL.md": "ab54ccc9d57d659a3ee5c71d37d66e8fb4b1777a6328f858f1f98a8f52ed9a44",
  "skills/clean-development/agents/openai.yaml": "1b036ae4fc8010eb0647a77c294dc1bec10f9da5a3960e52c51afe0d728cd050",
  "package.json": "0acc9504a83b6db155e59290af520138bc166d70593039c5efc91ae9569ab27b",
  "assets/logo.png": "f3db98f6eff3c9423136376107c5e756c77e232dd3b7a311e69822b92c44d0ff",
  "integrations/claude/hooks.json": "39850f4a219784ce099cba79c874719dc53cf4397abfcaa115c570a1e0091baf",
  "hooks/session-start": "4786714abfc76e78e82aa3d423e6988858f58dee3f252d736935081f6adb73e5",
  "bin/clean-development.js": "1615653b475b8a38f399e0c877aaa9491aa6546962e7ede9d6550081a6713df8",
  "bin/clean-development-shim.js": "ac89ce47db59e3c71fe6949c3745a9648638a15cc3624b877214cb50dd480ddf",
  "schemas/project-config.schema.json": "5172e77905d4d30ccf35ea614917d8cd0fedec500187eef902b403260d91d822",
  "src/adapters.js": "082c82493fe0ee3d83c9982a25278f521b7f16853873812fc938d5a087c82ef3",
  "src/cli.js": "4a4bd0d28638d14fc295164db46cc764f1a46db39899f93b49f96c611ac4281e",
  "src/config.js": "f877c157cc08521be1cb25668e55b71a5a3adc416085db694f09a6cab5b8eb70",
  "src/constants.js": "bf176362272000b492a7f13759461f0feebc69081db1d892a4a7db5e840a0307",
  "src/index.js": "252223fe0b087ab22a13e0e53847f55f803b2821f58a3c9aa6c517f548831290",
  "src/integrations.js": "d5cac065119971d7186df0b49a0626dd6507d41b3cf7dee339a92be5d58efdf8",
  "src/io.js": "7801e783333cdd0703daf9ef8d70046ccbadf9022da7824271ad93a185def277",
  "src/platform.js": "ad8a1cde7051722bd237e48aeeb493141ced2e26ef8b39056530523bef4a3826",
  "src/runtime.js": "1a7c9749a89676c72901b307d3856c670c6d11eb15ee024cd7de5c58cb4a2fa2",
  "src/session.js": "1d42b0756ba40c9b0c829448cc543b8845d090ce962c7d1cb3af3ca61bf24bff",
  "src/state.js": "b004359daac2cc4da4698b0043a248964073949aece27063e36328871cc3a367",
  "src/workspace.js": "9d06ea90a6ab271602ee795dbd197689a3b5a3adfc05063000a9cf3d919bdb94"
};

test("isolated installed runtime package provenance matches clean-development 0.2.1 source", (t) => {
  const { plugin } = fixture(t);
  for (const [relative, digest] of Object.entries(expected)) {
    const actual = crypto.createHash("sha256").update(fs.readFileSync(path.join(plugin, relative))).digest("hex");
    assert.equal(actual, digest, relative);
  }
  assert.deepEqual(
    fs.readdirSync(path.join(plugin, "src")).sort(),
    Object.keys(expected).filter((file) => file.startsWith("src/")).map((file) => file.slice(4)).sort()
  );
});
