"use strict";
const Module = require("node:module");
const fs = require("node:fs");
const path = require("node:path");
const net = require("node:net");
const builtins = new Set(Module.builtinModules.flatMap((name) => [name, `node:${name}`]));
const originalLoad = Module._load;
const skillRoot = fs.realpathSync(process.env.ISOLATED_SKILL_ROOT);
function blocked(reason) {
  fs.appendFileSync(process.env.ISOLATED_AUDIT_FILE, `${reason}\n`);
  throw new Error(reason);
}
Module._load = function (request, parent, isMain) {
  if (!builtins.has(request)) {
    if (!request.startsWith(".") && !path.isAbsolute(request)) {
      blocked(`external dependency: ${request}`);
    }
    const filename = Module._resolveFilename(request, parent, isMain);
    const relative = path.relative(skillRoot, fs.realpathSync(filename));
    if (relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative) ||
        relative.split(path.sep).includes("node_modules")) blocked("module outside isolated Skill");
  }
  return originalLoad.apply(this, arguments);
};
net.Socket.prototype.connect = function () {
  return blocked("network attempt during offline package test");
};
