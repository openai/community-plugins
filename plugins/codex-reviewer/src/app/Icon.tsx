const paths = {
  folder: "M2.5 5.5h5l2 2h12v12h-19z M2.5 5.5V4h6l2 2h11v1.5",
  file: "M6 2.5h8l4 4v15H6z M14 2.5v5h4",
  branch: "M6 6v12 M18 6v3a6 6 0 0 1-6 6H6 M6 3a3 3 0 1 0 0 6 3 3 0 0 0 0-6 M6 15a3 3 0 1 0 0 6 3 3 0 0 0 0-6 M18 3a3 3 0 1 0 0 6 3 3 0 0 0 0-6",
  refresh: "M20 7v5h-5 M4 17v-5h5 M19 11a7 7 0 0 0-12-5L4 9 M5 13a7 7 0 0 0 12 5l3-3",
  chevron: "m9 5 7 7-7 7",
  diff: "M8 3H4v18h4 M16 3h4v18h-4 M10 8h4 M12 6v4 M10 16h4",
};
export function Icon({ name, size = 16 }: { name: keyof typeof paths; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}
