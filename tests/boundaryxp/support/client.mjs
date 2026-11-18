import { spawn } from 'node:child_process';
import readline from 'node:readline';

export class McpClient {
  constructor(command, args, options) {
    this.pending = new Map(); this.seq = 0; this.stderr = '';
    this.child = spawn(command, args, { ...options, shell: false, windowsHide: true, stdio:['pipe','pipe','pipe'] });
    this.exited = new Promise(resolve => this.child.once('exit', (code, signal) => { this.exit = {code,signal}; this.fail(new Error(`MCP exited: ${code ?? signal}; ${this.stderr.slice(-2000)}`)); resolve(this.exit); }));
    this.child.once('error', error => this.fail(error));
    this.child.stderr.on('data', data => { this.stderr = (this.stderr + data).slice(-8000); });
    this.lines = readline.createInterface({ input: this.child.stdout });
    this.lines.on('line', line => {
      let msg; try { msg = JSON.parse(line); } catch { this.fail(new Error('Non-JSON stdout: '+line.slice(0,120))); return; }
      const pending = this.pending.get(msg.id);
      if (pending) { clearTimeout(pending.timer); this.pending.delete(msg.id); msg.error ? pending.reject(new Error(JSON.stringify(msg.error))) : pending.resolve(msg.result); }
    });
  }
  fail(error) { for (const p of this.pending.values()) { clearTimeout(p.timer); p.reject(error); } this.pending.clear(); }
  request(method, params = {}) {
    return new Promise((resolve,reject) => {
      const id = ++this.seq;
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error(`Timeout: ${method}; ${this.stderr.slice(-2000)}`)); }, 290_000);
      this.pending.set(id,{resolve,reject,timer});
      this.child.stdin.write(JSON.stringify({jsonrpc:'2.0',id,method,params})+'\n');
    });
  }
  async connect() {
    const result = await this.request('initialize',{protocolVersion:'2024-11-05',capabilities:{},clientInfo:{name:'boundaryxp-plugin-package-test',version:'1.0.0'}});
    this.child.stdin.write(JSON.stringify({jsonrpc:'2.0',method:'notifications/initialized'})+'\n');
    return result;
  }
  async call(name, args = {}) {
    const out = await this.request('tools/call',{name,arguments:args});
    if (out.isError) throw new Error(JSON.stringify(out));
    return out.structuredContent ?? JSON.parse(out.content.find(x => x.type === 'text').text);
  }
  async close() {
    this.child.stdin.end();
    const timer = setTimeout(() => this.child.kill(), 12_000);
    try { await this.exited; } finally { clearTimeout(timer); this.lines.close(); }
  }
}
