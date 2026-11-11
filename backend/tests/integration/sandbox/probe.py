"""A network probe that runs inside sandbox containers (Node, no dependencies).

It prints one JSON object: for each ``proxy:<host>`` the proxy's status line for a ``CONNECT``,
and for each ``direct:<host>:<port>`` whether a plain TCP connection opened.
"""

from __future__ import annotations

import json

PROBE_JS = r"""
const net = require('net');
const dns = require('dns');
const [proxyHost, proxyPort, ...targets] = process.argv.slice(1);
function viaProxy(host) {
  return new Promise((resolve) => {
    const s = net.connect(Number(proxyPort), proxyHost, () =>
      s.write(`CONNECT ${host}:443 HTTP/1.1\r\nHost: ${host}:443\r\n\r\n`));
    s.once('data', (d) => { resolve(d.toString().split('\r\n')[0]); s.destroy(); });
    s.on('error', (e) => resolve('ERR ' + e.code));
    setTimeout(() => { resolve('TIMEOUT'); s.destroy(); }, 15000);
  });
}
function direct(host, port) {
  return new Promise((resolve) => {
    const s = net.connect(Number(port), host, () => { resolve('OPEN'); s.destroy(); });
    s.on('error', (e) => resolve('ERR ' + e.code));
    setTimeout(() => { resolve('TIMEOUT'); s.destroy(); }, 5000);
  });
}
(async () => {
  const out = {};
  for (const t of targets) {
    const [kind, host, port] = t.split(':');
    out[t] = kind === 'proxy' ? await viaProxy(host) : await direct(host, port);
  }
  console.log(JSON.stringify(out));
})();
"""


def probe_args(proxy_host: str, proxy_port: int, targets: list[str]) -> list[str]:
    return ["node", "-e", PROBE_JS, proxy_host, str(proxy_port), *targets]


def parse_probe(stdout: bytes) -> dict[str, str]:
    lines = stdout.decode("utf-8", "replace").strip().splitlines()
    data: dict[str, str] = json.loads(lines[-1])
    return data
