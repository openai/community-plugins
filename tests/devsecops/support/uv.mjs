export function requireUvVersion(output) {
  if (!/^uv 0\.13\.7(?: \([^\r\n]+\))?\r?\n?$/.test(output))
    throw new Error('The master requires uv 0.13.7; install that pinned version before test setup. No dependency acquisition was started.');
}
