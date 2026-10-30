export function pruneExpiredSessions(records, nowMs) {
  const active = [];
  for (const record of records) {
    if (typeof record.expiresAtMs === "number" && record.expiresAtMs > nowMs) {
      active.push(record);
    }
  }
  return active;
}
