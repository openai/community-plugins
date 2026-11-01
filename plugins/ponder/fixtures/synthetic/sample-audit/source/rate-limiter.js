export function checkWindowAllowance(counters, bucketKey, nowMs, limit = 60) {
  const entry = counters.get(bucketKey) ?? { count: 0, windowStartMs: nowMs };
  if (nowMs - entry.windowStartMs >= 60_000) {
    entry.count = 0;
    entry.windowStartMs = nowMs;
  }
  entry.count += 1;
  counters.set(bucketKey, entry);
  return {
    allowed: entry.count <= limit,
    remaining: Math.max(0, limit - entry.count),
  };
}
