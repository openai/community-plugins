const records = [
  { id: "w1", name: "Alpha" },
  { id: "w2", name: "Beta" },
];

export function listWidgets({ limit = 10 } = {}) {
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
    throw new RangeError("limit must be an integer from 1 to 100");
  }
  return { widgets: records.slice(0, limit), nextCursor: null };
}
