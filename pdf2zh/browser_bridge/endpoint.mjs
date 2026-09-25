import fs from 'node:fs/promises';
import path from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const runFile = promisify(execFile);

async function readJson(filename) {
  try { return JSON.parse(await fs.readFile(filename, 'utf8')); } catch { return null; }
}

async function verify(port, expectedSocket) {
  if (!Number.isInteger(port) || port < 1 || port > 65535) return null;
  try {
    const endpoint = `http://127.0.0.1:${port}`;
    const response = await fetch(`${endpoint}/json/version`, { signal: AbortSignal.timeout(1800) });
    if (!response.ok) return null;
    const version = await response.json();
    const socket = new URL(version.webSocketDebuggerUrl);
    if (!/^Chrome\//.test(version.Browser || '') || socket.protocol !== 'ws:' ||
        !['127.0.0.1', 'localhost'].includes(socket.hostname) || Number(socket.port) !== port ||
        !socket.pathname.startsWith('/devtools/browser/') ||
        (expectedSocket && socket.pathname !== expectedSocket)) return null;
    return { endpoint, port, socketPath: socket.pathname };
  } catch { return null; }
}

async function discoverOwnedWindowsPorts(profile) {
  if (process.platform !== 'win32') return [];
  // Inspect only Chrome main processes with this exact --user-data-dir. Never
  // discover arbitrary debug ports or attach to the user's personal profile.
  const script = String.raw`
$ErrorActionPreference = 'Stop'
$wantedProfile = [IO.Path]::GetFullPath($env:PDF2ZH_ENDPOINT_PROFILE).TrimEnd('\')
$ownedIds = @(Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | Where-Object {
  $line = $_.CommandLine
  $match = [regex]::Match($line, '(?:^|\s)--user-data-dir=(?:"([^"]+)"|(\S+))')
  if (!$match.Success -or $line -match '(?:^|\s)--type=') { return $false }
  $value = if ($match.Groups[1].Success) { $match.Groups[1].Value } else { $match.Groups[2].Value }
  try { [IO.Path]::GetFullPath($value).TrimEnd('\') -ieq $wantedProfile } catch { $false }
} | ForEach-Object { $_.ProcessId })
if ($ownedIds.Count -eq 0) { Write-Output '[]'; exit }
$ports = @(Get-NetTCPConnection -State Listen | Where-Object {
  $_.LocalAddress -eq '127.0.0.1' -and $_.OwningProcess -in $ownedIds
} | Select-Object -ExpandProperty LocalPort -Unique)
ConvertTo-Json -InputObject $ports -Compress
`;
  const shell = path.join(process.env.SystemRoot || 'C:/Windows', 'System32/WindowsPowerShell/v1.0/powershell.exe');
  try {
    const { stdout } = await runFile(shell, ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script], {
      env: { ...process.env, PDF2ZH_ENDPOINT_PROFILE: profile }, windowsHide: true,
      timeout: 8000, maxBuffer: 32768, encoding: 'utf8',
    });
    const values = JSON.parse(stdout.trim());
    return Array.isArray(values) ? values.filter(Number.isInteger).slice(0, 4) : [];
  } catch { return []; }
}

export async function browserEndpoint(stateDir, { discover = discoverOwnedWindowsPorts } = {}) {
  const profile = path.join(stateDir, 'chrome-profile');
  const active = await fs.readFile(path.join(profile, 'DevToolsActivePort'), 'utf8').catch(() => '');
  const [portText, socketPath] = active.trim().split(/\r?\n/);
  if (socketPath?.startsWith('/devtools/browser/')) {
    const current = await verify(Number(portText), socketPath);
    if (current) return current;
  }
  const cachePath = path.join(stateDir, 'browser-endpoint.json');
  const cached = await readJson(cachePath);
  if (cached?.socketPath?.startsWith('/devtools/browser/')) {
    const current = await verify(cached.port, cached.socketPath);
    if (current) return current;
  }
  if (!await fs.stat(profile).then(s => s.isDirectory()).catch(() => false)) return null;
  for (const port of await discover(profile)) {
    const current = await verify(port);
    if (!current) continue;
    const temp = `${cachePath}.${process.pid}.tmp`;
    await fs.writeFile(temp, JSON.stringify({ port: current.port, socketPath: current.socketPath }), { mode: 0o600 });
    await fs.rename(temp, cachePath);
    return current;
  }
  return null;
}
