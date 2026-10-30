export function verifyRequestHeader(headerValue, activeKeys) {
  if (typeof headerValue !== "string" || !headerValue.startsWith("Bearer ")) {
    return { ok: false, reason: "MISSING_BEARER_PREFIX" };
  }
  const keyId = headerValue.slice("Bearer ".length).trim();
  if (!keyId || !activeKeys.has(keyId)) {
    return { ok: false, reason: "UNKNOWN_KEY_ID" };
  }
  return { ok: true, keyId };
}
