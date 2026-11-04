import { createServer } from 'node:http';
import { basename, delimiter, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';

const markers = process.argv.slice(2);
if (!markers.length) throw new Error('Supply at least one absolute benchmark marker.');
const client = new Client({name: 'lighthouse-native-ui-preview', version: '1'});
const command = fileURLToPath(new URL('../dist/server.cjs', import.meta.url));
await client.connect(new StdioClientTransport({command: process.execPath, args: [command], env: {...process.env, LIGHTHOUSE_BENCHMARKS: markers.join(delimiter), LIGHTHOUSE_BENCHMARK_HOME: '/nonexistent/lighthouse-test-home'}, stderr: 'inherit'}));
const resource = async (uri) => {
  const result = await client.readResource({uri});
  const html = result.contents.find((content) => typeof content.text === 'string')?.text;
  if (html === undefined) throw new Error(`The plugin returned no HTML for ${uri}.`);
  return html;
};
const [homeWidget, threadWidget] = await Promise.all([resource('ui://lighthouse/explorer-home'), resource('ui://lighthouse/explorer-thread')]);
const aliases = new Map();
const allowedPaths = new Set();
for (const marker of markers) {
  const folder = basename(marker) === 'benchmark.lighthouse' ? dirname(marker) : marker;
  const alias = basename(folder);
  allowedPaths.add(marker);
  allowedPaths.add(folder);
  aliases.set(alias, aliases.has(alias) ? null : marker);
}
const calls = new Set(['lighthouse_explorer_library', 'lighthouse_explorer_snapshot', 'lighthouse_explorer_open_evidence', 'lighthouse_explorer_authoring_context', 'lighthouse_explorer_authoring_snapshot', 'lighthouse_explorer_attach']);
const panes = new Set(['instruction', 'rubric', 'evaluation']);

function launchFor(url) {
  const surface = url.searchParams.get('surface') === 'thread' ? 'thread' : 'home';
  if (surface === 'home') return {surface, launch: {}};
  const alias = url.searchParams.get('benchmark');
  const taskId = url.searchParams.get('task');
  const pane = url.searchParams.get('pane');
  if (!alias && (taskId || pane)) throw new Error('Choose a configured benchmark for a focused authoring panel.');
  if (!alias) return {surface, launch: {}};
  const marker = aliases.get(alias);
  if (!marker) throw new Error('This benchmark is unknown or ambiguous in the browser test bridge.');
  if (taskId && !/^[a-zA-Z0-9][a-zA-Z0-9._-]*$/.test(taskId)) throw new Error('The task name is invalid.');
  if (pane && !panes.has(pane)) throw new Error('The authoring pane is invalid.');
  return {surface, launch: {benchmarkPath: marker, ...(taskId ? {taskId} : {}), ...(pane ? {pane} : {})}};
}

const scriptValue = (value) => JSON.stringify(value).replaceAll('<', '\\u003c');

const shell = ({surface, launch}) => `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Lighthouse native UI preview</title><style>html,body,iframe{margin:0;width:100%;height:100%;border:0;display:block}</style></head><body><iframe title="Lighthouse native app" src="/widget?surface=${surface}"></iframe><script>
const surface=${scriptValue(surface)};
const launch=${scriptValue(launch)};
const frame=document.querySelector('iframe');
const answer=(event,value)=>event.source.postMessage({jsonrpc:'2.0',id:event.data.id,result:value},location.origin);
function parsePath(path){if(typeof path!=='string'||!path.startsWith('/')||path.startsWith('//')||path.includes('#')||path.includes(String.fromCharCode(92))||Array.from(path).some(character=>character.charCodeAt(0)<=32||character.charCodeAt(0)===127))return null;try{const route=new URL(path,'https://lighthouse.invalid');decodeURIComponent(route.pathname);return route.origin==='https://lighthouse.invalid' ? route.pathname+route.search : null;}catch{return null;}}
let currentPath=parsePath(new URL(location.href).searchParams.get('path'))??'/';
const hostContext=()=>({theme:'light',displayMode:surface==='thread'?'fullscreen':'inline','openai/deepLink':{url:currentPath}});
const notifyPath=()=>frame.contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/host-context-changed',params:hostContext()},location.origin);
const shellUrl=(path)=>{const shell=new URL(location.href);if(path==='/')shell.searchParams.delete('path');else shell.searchParams.set('path',path);return shell;};
history.replaceState({lighthousePath:currentPath},'',shellUrl(currentPath));
window.addEventListener('popstate',event=>{currentPath=parsePath(event.state?.lighthousePath)??parsePath(new URL(location.href).searchParams.get('path'))??'/';notifyPath();});
function selfPath(value){if(typeof value!=='string')return null;try{const url=new URL(value);if(url.protocol!=='codex:'||url.host!=='plugins'||url.pathname!=='/self/app/lighthouse_explorer_home'||url.username||url.password||url.port||url.hash||url.searchParams.getAll('path').length>1)return null;return parsePath(url.searchParams.get('path')??'/');}catch{return null;}}
window.addEventListener('message',async event=>{if(event.source!==frame.contentWindow||event.origin!==location.origin||event.data?.jsonrpc!=='2.0')return;const m=event.data;
if(m.method==='ui/initialize'){answer(event,{protocolVersion:'2026-01-26',hostInfo:{name:'Lighthouse native surface test bridge',version:'1'},hostCapabilities:{serverTools:{},experimental:{'openai/selfDeepLinks':{}}},hostContext:hostContext()});return;}
if(m.method==='ui/notifications/initialized'){frame.contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-input',params:{arguments:launch}},location.origin);return;}
if(m.method==='tools/call'){const response=await fetch('/call',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(m.params)});answer(event,await response.json());return;}
if(m.method==='ui/open-link'){const next=selfPath(m.params?.url);if(next!==null){answer(event,{isError:false});if(next!==currentPath){currentPath=next;history.pushState({lighthousePath:currentPath},'',shellUrl(currentPath));notifyPath();}return;}}
if(m.id!==undefined)answer(event,{});});</script></body></html>`;

const server=createServer(async(request,response)=>{
  const address=server.address();
  if(!address||typeof address==='string'){response.writeHead(503).end();return;}
  const port=address.port;
  if(request.headers.host!==`127.0.0.1:${port}`){response.writeHead(403).end();return;}
  const url=new URL(request.url,`http://127.0.0.1:${port}`);
  if(url.pathname==='/'&&request.method==='GET'){
    try { const html = shell(launchFor(url)); response.writeHead(200,{'content-type':'text/html'}).end(html); }
    catch (error) { response.writeHead(400,{'content-type':'text/plain'}).end(error.message); }
    return;
  }
  if(url.pathname==='/widget'&&request.method==='GET'){response.writeHead(200,{'content-type':'text/html'}).end(url.searchParams.get('surface')==='thread' ? threadWidget : homeWidget);return;}
  if(url.pathname==='/call'&&request.method==='POST'){
    if(request.headers.origin!==`http://127.0.0.1:${port}`){response.writeHead(403).end();return;}
    try{let body='';for await(const data of request){body+=data;if(body.length>10000)throw new Error('Request too large');}const input=JSON.parse(body);
      if(!calls.has(input.name))throw new Error('This native-only tool is unavailable in the browser test bridge.');
      if(input.name==='lighthouse_explorer_attach'&&!allowedPaths.has(input.arguments?.path))throw new Error('The browser test bridge may attach configured benchmarks only.');
      const result=await client.callTool(input);response.writeHead(200,{'content-type':'application/json'}).end(JSON.stringify(result));
    }catch(error){response.writeHead(400,{'content-type':'application/json'}).end(JSON.stringify({isError:true,content:[{type:'text',text:error.message}]}));}return;
  }
  response.writeHead(404).end();
});
server.listen(Number(process.env.PORT??4178),'127.0.0.1',()=>process.stderr.write(`Native-surface browser test bridge: http://127.0.0.1:${server.address().port}\n`));
for(const signal of ['SIGINT','SIGTERM'])process.once(signal,()=>{server.close();void client.close();});
