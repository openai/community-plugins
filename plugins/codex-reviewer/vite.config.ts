import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

export default defineConfig({
  root: "src/app",
  base: "./",
  plugins: [viteSingleFile()],
  build: {
    outDir: "../../dist/app",
    emptyOutDir: false,
    assetsInlineLimit: 10_000_000,
  },
});
