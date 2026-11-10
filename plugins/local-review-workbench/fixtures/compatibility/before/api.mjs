const records = [
  { id: "w1", name: "Alpha" },
  { id: "w2", name: "Beta" },
];

export function listWidgets() {
  return { items: records.slice(0, 10), nextCursor: null };
}
