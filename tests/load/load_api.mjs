// P6-07: concurrent read load on the api without model calls. Each virtual user loops over the requests a chat
// user makes besides talking to the model: conversation list, message history, chat search, profile.
// Usage (from App): node tests/load/load_api.mjs <users> <seconds>   (stack running; fixture via docker exec)
import { execFileSync, spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
const [users = 20, seconds = 60] = process.argv.slice(2).map(Number);
const BASE = process.env.KAI_LOAD_BASE || 'http://localhost:3080', API = process.env.KAI_TEST_API_CONTAINER || 'kai-api-1';
const fixture = (...args) => execFileSync('docker', ['exec', '-i', API, 'node', '-', ...args], { input: readFileSync(new URL('./fixture.cjs', import.meta.url)), encoding: 'utf8', maxBuffer: 64 << 20 });

const stats = new Map(); // name -> { ms: [], errors }
const record = (name, ms, ok) => { const s = stats.get(name) ?? { ms: [], errors: 0 }; s.ms.push(ms); if (!ok) s.errors++; stats.set(name, s); };
async function hit(name, path, token) {
  const t = performance.now();
  try { const r = await fetch(BASE + path, { headers: { Authorization: `Bearer ${token}` } }); await r.arrayBuffer(); record(name, performance.now() - t, r.ok); }
  catch { record(name, performance.now() - t, false); }
}
// Peak CPU/RAM per kai container while the load runs (docker stats streams every ~2 s).
export function sampleStats() {
  const peak = new Map(), proc = spawn('docker', ['stats', '--format', '{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}']);
  let buf = '';
  proc.stdout.on('data', (d) => {
    buf += d; const lines = buf.split('\n'); buf = lines.pop();
    for (const line of lines) {
      const [name, cpu, mem] = line.replace(/\x1b\[[0-9;]*[A-Za-z]/g, '').split('|');
      if (!name?.startsWith('kai-') || !mem) continue;
      const [v, unit] = mem.split('/')[0].trim().match(/([\d.]+)(\w+)/).slice(1);
      const mib = +v * ({ B: 1 / 1048576, KiB: 1 / 1024, MiB: 1, GiB: 1024 }[unit] ?? 1);
      const p = peak.get(name) ?? { cpu: 0, mib: 0 };
      peak.set(name, { cpu: Math.max(p.cpu, parseFloat(cpu)), mib: Math.max(p.mib, mib) });
    }
  });
  return () => { proc.kill(); return peak; };
}
export const printPeaks = (peak) => { for (const [n, p] of [...peak].sort()) if (p.cpu > 1 || p.mib > 50) console.log(`  ${n.padEnd(22)} peak cpu=${p.cpu.toFixed(0)}% mem=${p.mib.toFixed(0)}MiB`); };
const pct = (a, p) => a.length ? a.sort((x, y) => x - y)[Math.min(a.length - 1, Math.floor(a.length * p))] : NaN;

let vus;
try {
  vus = JSON.parse(fixture('setup', String(users), '20').split('\n').find((l) => l.startsWith('KAI_FIXTURE ')).slice(12));
  const stop = sampleStats();
  const until = Date.now() + seconds * 1000;
  await Promise.all(vus.map(async (u) => {
    while (Date.now() < until) {
      await hit('convos', '/api/convos?limit=25', u.token);
      await hit('messages', `/api/messages/${u.conversationId}`, u.token);
      await hit('search', `/api/convos?search=${u.word}`, u.token);
      await hit('user', '/api/user', u.token);
    }
  }));
  let total = 0, errors = 0;
  console.log(`users=${users} seconds=${seconds}`);
  for (const [name, s] of stats) {
    total += s.ms.length; errors += s.errors;
    console.log(`${name.padEnd(9)} n=${String(s.ms.length).padStart(6)} p50=${pct(s.ms, 0.5).toFixed(0)}ms p95=${pct(s.ms, 0.95).toFixed(0)}ms p99=${pct(s.ms, 0.99).toFixed(0)}ms errors=${s.errors}`);
  }
  console.log(`total rps=${(total / seconds).toFixed(1)} errors=${errors}`);
  printPeaks(stop());
} finally {
  console.log(fixture('teardown'));
}
