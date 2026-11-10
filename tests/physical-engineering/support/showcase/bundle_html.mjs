// Keep JavaScript replacement tokens literal when embedding a compiled module.
export function buildOfflineHtml(template, data, javascript) {
  const entry = '<script type="module" src="/showcase.mjs"></script>';
  if (template.split(entry).length !== 2)
    throw Error("Expected exactly one showcase module entry");
  const safe = (s) =>
    s.replace(/<\/script/gi, (match) => "<\\/" + match.slice(2));
  return template
    .replace(/<script type="importmap">[\s\S]*?<\/script>/, "")
    .replace('src="/narration.wav"', "")
    .replace(
      entry,
      () =>
        `<script>window.__SHOWCASE__=${safe(JSON.stringify(data))};</script><script type="module">${safe(javascript)}</script>`,
    )
    .replace(
      "</head>",
      '<meta name="description" content="Physical Engineering: from the brief to a checked CAD handoff."></head>',
    );
}
