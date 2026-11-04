import { readFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { basename, dirname, isAbsolute, resolve } from 'node:path';
import { BenchmarkFiles } from '../src/benchmark/files.ts';
import { getExplorerSnapshot } from '../src/snapshot.ts';

const htmlType = 'text/html; charset=utf-8';
const jsonType = 'application/json; charset=utf-8';

export function parseMarkers(arguments_) {
  const markers = new Map();
  for (const item of arguments_) {
    const separator = item.indexOf('=');
    const name = item.slice(0, separator);
    const path = item.slice(separator + 1);
    if (separator < 1 || !/^[a-z0-9][a-z0-9-]*$/.test(name) || !isAbsolute(path)) {
      throw new Error('Supply repo-id=/absolute/path/to/benchmark.lighthouse (lowercase letters, numbers and hyphens).');
    }
    if (markers.has(name)) throw new Error(`Duplicate benchmark: ${name}`);
    markers.set(name, path);
  }
  if (markers.size === 0) throw new Error('Supply at least one benchmark marker.');
  return markers;
}

function html(value) {
  return value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
}

function title(id) {
  return id.split(/[-_ ]+/).filter(Boolean).map((part) => `${part[0].toUpperCase()}${part.slice(1)}`).join(' ') || 'Benchmark';
}

function dashboard(markers, active) {
  const names = new Map([...markers].map(([id, marker]) => [id, title(id === 'benchmark' ? basename(dirname(marker)) : id)]));
  const links = [...names].map(([name, label]) => `<a href="/?repo=${encodeURIComponent(name)}"${name === active ? ' aria-current="page"' : ''}>${html(label)}</a>`).join('');
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${html(names.get(active))} · Lighthouse Explorer</title><style>
  :root{color-scheme:light;--ink:#20392f;--muted:#5b6b62;--line:#dde5df;--canvas:#f6f8f5;--white:#fff;--accent:#176547}*{box-sizing:border-box}body{margin:0;background:var(--canvas);color:var(--ink);font:14px system-ui,sans-serif;display:grid;grid-template-rows:auto 1fr;height:100vh}header{min-height:49px;background:var(--white);border-bottom:1px solid var(--line);padding:0 24px;display:flex;align-items:center;gap:18px}header .brand{color:var(--muted);white-space:nowrap;font-size:12px}header nav{display:flex;gap:4px;align-items:center;overflow:auto}header a{border-radius:6px;color:var(--muted);font-weight:600;padding:6px 10px;text-decoration:none;white-space:nowrap}header a:hover,header a[aria-current=page]{color:var(--accent);background:#eaf3ed}header .mode{margin-left:auto;white-space:nowrap;color:var(--muted);font-size:11px}#dashboard{display:block;border:0;width:100%;height:100%;min-height:0;background:var(--canvas)}dialog{position:fixed;inset:0;margin:auto;padding:0;width:min(900px,92vw);height:min(760px,88vh);border:1px solid var(--line);border-radius:14px;box-shadow:0 20px 70px #17352635;background:var(--white);color:var(--ink)}dialog::backdrop{background:#162c2370}dialog[open]{display:grid;grid-template-rows:auto 1fr}dialog header{padding:12px 20px;gap:12px}dialog h2{margin:0;font-size:14px}#evidence-path{color:var(--muted);font-size:11px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}dialog button{margin-left:auto;background:white;color:var(--ink);border:1px solid var(--line);padding:6px 10px;border-radius:6px;cursor:pointer;white-space:nowrap}#evidence{width:100%;height:100%;border:0;min-height:0}@media(max-width:540px){header{padding:0 10px;gap:6px}.brand,.mode{display:none}dialog header{padding:9px;gap:6px}}
  </style></head><body><header><span class="brand">Local benchmarks</span><nav aria-label="Benchmarks">${links}</nav><span class="mode">Read only · stays on this computer</span></header><iframe id="dashboard" title="Lighthouse Explorer" src="/widget"></iframe><dialog id="evidence-dialog" aria-labelledby="evidence-title"><header><h2 id="evidence-title">Source evidence</h2><span id="evidence-path"></span><button type="button" id="close-evidence">Close</button></header><iframe id="evidence" title="Source evidence"></iframe></dialog><script>
  const repo=new URLSearchParams(location.search).get('repo');const frame=document.querySelector('#dashboard');const evidence=document.querySelector('#evidence');const dialog=document.querySelector('#evidence-dialog');
  document.querySelector('#close-evidence').addEventListener('click',()=>dialog.close());dialog.addEventListener('close',()=>{evidence.removeAttribute('src');frame.focus()});
  const reply=(source,id,result,error)=>source.postMessage({jsonrpc:'2.0',id,...(error?{error:{code:-32000,message:String(error)}}:{result})},location.origin);
  const send=(method,params)=>frame.contentWindow.postMessage({jsonrpc:'2.0',method,params},location.origin);
  const fetchData=async(path)=>{const response=await fetch(path);const value=await response.json();if(!response.ok)throw new Error(value.error);return value;};
  window.addEventListener('message',async event=>{if(event.source!==frame.contentWindow||event.origin!==location.origin)return;const message=event.data;if(!message||message.jsonrpc!=='2.0')return;
  try{if(message.method==='ui/initialize'){reply(event.source,message.id,{protocolVersion:'2026-01-26',hostInfo:{name:'Lighthouse Explorer',version:'1'},hostCapabilities:{serverTools:{},experimental:{'openai/files':{}}},hostContext:{theme:'light',displayMode:'fullscreen'}});return;}
  if(message.method==='ui/notifications/initialized'){send('ui/notifications/tool-input',{arguments:{file:{name:'benchmark.lighthouse',resourceUri:'host-resource://'+repo}}});return;}
  if(message.method==='tools/call'){if(message.params.name==='lighthouse_explorer_snapshot'){const value=await fetchData('/snapshot?repo='+encodeURIComponent(repo));reply(event.source,message.id,{content:[],structuredContent:value});return;}if(message.params.name==='lighthouse_explorer_open_evidence'){const value=await fetchData('/resolve-evidence?repo='+encodeURIComponent(repo)+'&path='+encodeURIComponent(message.params.arguments.path));reply(event.source,message.id,{content:[],structuredContent:value});return;}}
  if(message.method==='openai/files/open'){const path=message.params.path;document.querySelector('#evidence-path').textContent=path;evidence.src='/evidence?repo='+encodeURIComponent(repo)+'&path='+encodeURIComponent(path);if(!dialog.open)dialog.showModal();reply(event.source,message.id,{});return;}
  if(message.id!==undefined)reply(event.source,message.id,{});
  }catch(error){if(message.id!==undefined)reply(event.source,message.id,undefined,error.message);}});
  </script></body></html>`;
}

function evidencePage(path, text) {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${html(path)}</title><style>:root{color-scheme:light}body{margin:0;padding:22px;color:#263a31;background:white;font:13px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace}pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:0}</style></head><body><pre>${html(text)}</pre></body></html>`;
}

function output(response, status, type, value, headers = {}) {
  response.writeHead(status, {
    'content-type': type,
    'cache-control': 'no-store',
    'x-content-type-options': 'nosniff',
    'referrer-policy': 'no-referrer',
    ...headers,
  });
  response.end(value);
}

function json(response, status, value) {
  return output(response, status, jsonType, JSON.stringify(value));
}

export function createPreviewServer({ markers, widget, snapshot = getExplorerSnapshot }) {
  if (markers.size === 0) throw new Error('Supply at least one benchmark marker.');
  const server = createServer(async (request, response) => {
    const origin = `http://127.0.0.1:${server.address().port}`;
    if (request.headers.host !== new URL(origin).host) return json(response, 403, { error: 'This viewer is available only on its loopback address.' });
    if (request.method !== 'GET') return json(response, 405, { error: 'This viewer is read-only.' });
    try {
      const url = new URL(request.url ?? '/', origin);
      if (url.origin !== origin) return json(response, 403, { error: 'Cross-origin requests are not accepted.' });
      if (url.pathname === '/') {
        const repo = url.searchParams.get('repo');
        if (!repo) return output(response, 302, htmlType, '', { location: `/?repo=${markers.keys().next().value}` });
        if (!markers.has(repo)) return output(response, 404, htmlType, evidencePage('Benchmark not found', 'This benchmark is not part of the local viewer.'));
        return output(response, 200, htmlType, dashboard(markers, repo));
      }
      if (url.pathname === '/widget') return output(response, 200, htmlType, widget);
      if (url.pathname === '/snapshot' || url.pathname === '/resolve-evidence' || url.pathname === '/evidence') {
        if (request.headers.origin && request.headers.origin !== origin || request.headers['sec-fetch-site'] === 'cross-site') {
          return json(response, 403, { error: 'Cross-origin requests are not accepted.' });
        }
        const marker = markers.get(url.searchParams.get('repo'));
        if (!marker) return json(response, 404, { error: 'Unknown benchmark.' });
        if (url.pathname === '/snapshot') return json(response, 200, await snapshot(marker));
        const requested = url.searchParams.get('path');
        if (!requested) return json(response, 400, { error: 'Choose an evidence file.' });
        try {
          const files = await BenchmarkFiles.fromMarker(marker);
          if (url.pathname === '/resolve-evidence') {
            if (!(await files.locate(requested))) return json(response, 404, { error: 'This evidence file was not found.' });
            return json(response, 200, { path: requested });
          }
          if (requested.endsWith('.png')) {
            if (!(await files.locate(requested))) return output(response, 404, htmlType, evidencePage(requested, 'This evidence file was not found.'));
            return output(response, 415, htmlType, evidencePage(requested, 'This local viewer currently displays text evidence only.'));
          }
          const content = await files.read(requested);
          if (content === null) return output(response, 404, htmlType, evidencePage(requested, 'This evidence file was not found.'));
          return output(response, 200, htmlType, evidencePage(requested, content), { 'content-security-policy': "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'" });
        } catch {
          return json(response, 403, { error: 'This evidence is unavailable or outside the selected benchmark.' });
        }
      }
      return output(response, 404, 'text/plain; charset=utf-8', 'Not found');
    } catch {
      return json(response, 500, { error: 'The local viewer could not read this benchmark.' });
    }
  });
  return server;
}

if (basename(process.argv[1] ?? '') === 'preview.mjs') {
  const markers = parseMarkers(process.argv.slice(2));
  const widgetPath = resolve(dirname(resolve(process.argv[1])), '../dist/explorer.html');
  readFile(widgetPath).then((widget) => {
    const server = createPreviewServer({ markers, widget });
    server.listen(Number(process.env.PORT ?? 4173), '127.0.0.1', () => {
      process.stderr.write(`Lighthouse Explorer ready at http://127.0.0.1:${server.address().port}/\n`);
    });
  }).catch((error) => {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  });
}
