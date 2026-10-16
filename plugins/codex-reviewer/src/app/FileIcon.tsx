import React, { type CSSProperties } from "react";
import seti from "./assets/seti/vs-seti-icon-theme.json";
import glyphs from "./assets/seti/glyphs.json";

type Associations = { file?: string; fileNames?: Record<string, string>; fileExtensions?: Record<string, string>; languageIds?: Record<string, string> };
const theme: Associations & { light: Associations; iconDefinitions: Record<string, { fontCharacter: string; fontColor?: string }> } = seti;
const outlines: Record<string, { viewBox: string; path: string }> = glyphs;
const lightRules: Associations = {
  file: theme.light.file ?? theme.file,
  fileNames: { ...theme.fileNames, ...theme.light.fileNames },
  fileExtensions: { ...theme.fileExtensions, ...theme.light.fileExtensions },
  languageIds: { ...theme.languageIds, ...theme.light.languageIds },
};
const languages: Record<string, string> = {
  ts: "typescript", tsx: "typescriptreact", js: "javascript", jsx: "javascriptreact", mjs: "javascript", cjs: "javascript",
  json: "json", jsonc: "jsonc", jsonl: "jsonl", py: "python", rs: "rust", go: "go", yaml: "yaml", yml: "yaml",
  md: "markdown", markdown: "markdown", html: "html", htm: "html", css: "css", scss: "scss", less: "less",
  sh: "shellscript", bash: "shellscript", zsh: "shellscript", sql: "sql", ps1: "powershell", bat: "bat", cmd: "bat",
  c: "c", h: "c", cc: "cpp", cpp: "cpp", hpp: "cpp", cs: "csharp", java: "java", rb: "ruby", php: "php",
  swift: "swift", kt: "kotlin", lua: "lua", r: "r", vue: "vue", xml: "xml", svg: "xml", toml: "toml",
};

function association(fileName: string, language: string, rules: Associations): string | undefined {
  if (rules.fileNames?.[fileName]) return rules.fileNames[fileName];
  for (let dot = fileName.indexOf("."); dot >= 0; dot = fileName.indexOf(".", dot + 1)) {
    const matched = rules.fileExtensions?.[fileName.slice(dot + 1)];
    if (matched) return matched;
  }
  return rules.languageIds?.[language];
}

export function resolveFileIcon(filePath: string) {
  const name = filePath.replaceAll("\\", "/").split("/").at(-1)!.toLowerCase();
  const language = name === "dockerfile" || name.startsWith("dockerfile.") ? "dockerfile" : name === "makefile" ? "makefile" : languages[name.split(".").at(-1)!] ?? "";
  const id = association(name, language, theme) ?? theme.file!;
  const lightId = association(name, language, lightRules) ?? lightRules.file!;
  const icon = theme.iconDefinitions[id];
  return { id, ...outlines[icon.fontCharacter.slice(1)], darkColor: icon.fontColor ?? "currentColor", lightColor: theme.iconDefinitions[lightId].fontColor ?? "currentColor" };
}

export function FileIcon({ path }: { path: string }) {
  const icon = resolveFileIcon(path);
  return <span className="file-icon" data-file-icon={icon.id} aria-hidden="true" style={{ "--file-icon-dark": icon.darkColor, "--file-icon-light": icon.lightColor } as CSSProperties}>
    <svg viewBox={icon.viewBox} width="16" height="16" fill="currentColor" focusable="false"><path d={icon.path} /></svg>
  </span>;
}
