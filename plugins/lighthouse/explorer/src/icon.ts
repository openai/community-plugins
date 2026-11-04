import type { Icon } from "@modelcontextprotocol/sdk/types.js";

function svg(color: string): string {
  const drawing = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none"><path d="M32 6 21 17v7h22v-7L32 6Z" stroke="${color}" stroke-width="3.5" stroke-linejoin="round"/><path d="M24 24 19 54h26l-5-30M16 54h32M27 17h10M29 34h6m-7 8h8M11 21H5m54 0h-6M15 13l-5-4m39 4 5-4" stroke="${color}" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(drawing)}`;
}

export const lighthouseIcons: Icon[] = [
  { src: svg("#1f513f"), mimeType: "image/svg+xml", sizes: ["any"], theme: "light" },
  { src: svg("#aee8c8"), mimeType: "image/svg+xml", sizes: ["any"], theme: "dark" },
];
