#!/usr/bin/env node
/**
 * Start everything RepoAtlas needs locally, in one terminal:
 *
 *   node scripts/dev.mjs
 *
 * 1. Postgres (docker compose; Docker Desktop must be running),
 * 2. database migrations,
 * 3. the API on http://localhost:8000, a build worker, and the web app on http://localhost:3000
 *    (or the next free ports, when other apps hold those; the web app and the API's CORS
 *    settings follow).
 *
 * Ctrl+C stops all three (the worker gets its stop file, so a running build is put back in the
 * queue). Installs missing dependencies first (`uv sync`, `npm ci`). Logs are prefixed by process.
 */

import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const backend = path.join(root, "backend");
const frontend = path.join(root, "frontend");
const windows = process.platform === "win32";
const env = { ...process.env };

// Docker Desktop on Windows doesn't always put its CLI on PATH.
const dockerBin = "C:\\Program Files\\Docker\\Docker\\resources\\bin";
if (windows && existsSync(dockerBin) && !(env.PATH ?? env.Path ?? "").includes(dockerBin)) {
  const key = "PATH" in env ? "PATH" : "Path";
  env[key] = `${env[key] ?? ""};${dockerBin}`;
}

const COLORS = { db: 36, api: 32, worker: 33, web: 35, setup: 90 };

function log(name, line) {
  process.stdout.write(`\x1b[${COLORS[name] ?? 37}m[${name}]\x1b[0m ${line}\n`);
}

/** Windows needs a shell to find `npm`/`uv` shims; quote arguments with spaces for it. */
function argv(args) {
  return windows ? args.map((arg) => (/[\s&()^|<>]/.test(arg) ? `"${arg}"` : arg)) : args;
}

function run(name, command, args, cwd) {
  log(name, `$ ${command} ${args.join(" ")}`);
  const result = spawnSync(command, argv(args), { cwd, env, stdio: "inherit", shell: windows });
  if (result.status !== 0) {
    log(name, `failed (exit ${result.status ?? result.signal}).`);
    process.exit(result.status ?? 1);
  }
}

const children = [];

function start(name, command, args, cwd) {
  const child = spawn(command, argv(args), { cwd, env, shell: windows });
  children.push(child);
  for (const stream of [child.stdout, child.stderr]) {
    let buffer = "";
    stream.on("data", (chunk) => {
      buffer += chunk.toString();
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() ?? "";
      for (const line of lines) if (line.trim()) log(name, line);
    });
  }
  child.on("exit", (code) => {
    log(name, `exited (${code ?? "signal"}).`);
    if (!stopping) stop(1);
  });
  return child;
}

let stopping = false;
const stopFile = path.join(backend, ".repoatlas", "worker.stop");

function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  log("setup", "Stopping…");
  try {
    mkdirSync(path.dirname(stopFile), { recursive: true });
    writeFileSync(stopFile, "stop\n");
  } catch {
    // The worker also stops on Ctrl+C.
  }
  setTimeout(() => {
    for (const child of children) {
      if (child.exitCode !== null) continue;
      if (windows) spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
      else child.kill("SIGTERM");
    }
    process.exit(code);
  }, 2500);
}

/** True when the port can be bound on `host` (undefined: the dual-stack wildcard). */
function bindable(port, host) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", () => resolve(false));
    server.once("listening", () => server.close(() => resolve(true)));
    if (host) server.listen(port, host);
    else server.listen(port);
  });
}

/**
 * True when nothing listens on the port. Each check alone misses a case on Windows (a wildcard
 * bind doesn't see a 127.0.0.1 listener and vice versa), so all three must pass.
 */
async function free(port) {
  for (const host of [undefined, "127.0.0.1", "::1"]) if (!(await bindable(port, host))) return false;
  return true;
}

async function firstFree(from) {
  for (let port = from; port < from + 20; port += 1) if (await free(port)) return port;
  throw new Error(`No free port between ${from} and ${from + 19}.`);
}

process.on("SIGINT", () => stop(0));
process.on("SIGTERM", () => stop(0));

run("setup", "uv", ["sync"], backend);
if (!existsSync(path.join(frontend, "node_modules", ".bin"))) {
  run("setup", "npm", ["ci", "--no-audit", "--no-fund"], frontend);
}
run("db", "docker", ["compose", "-f", path.join("infra", "docker-compose.yml"), "up", "-d", "--wait"], root);
run("db", "uv", ["run", "repoatlas", "db", "upgrade"], backend);

// Default ports, or the next free ones when another app holds them.
const apiPort = await firstFree(8000);
const webPort = await firstFree(3000);
env.REPOATLAS_WEB_ORIGINS = `http://localhost:${webPort},http://127.0.0.1:${webPort}`;
// Local development rebuilds and asks freely: no per-day caps unless the shell sets them.
env.REPOATLAS_BUILDS_PER_DAY ??= "0";
env.REPOATLAS_ASKS_PER_DAY ??= "0";
env.NEXT_PUBLIC_API_URL = `http://localhost:${apiPort}`;

start("api", "uv", ["run", "uvicorn", "repoatlas.api.app:create_app", "--factory", "--port", String(apiPort)], backend);
start("worker", "uv", ["run", "repoatlas", "worker", "--stop-file", stopFile], backend);
start("web", "npm", ["run", "dev", "--", "--port", String(webPort)], frontend);

setTimeout(() => {
  log("setup", `RepoAtlas is starting: open http://localhost:${webPort} (API on http://localhost:${apiPort}).`);
  log("setup", "Press Ctrl+C to stop everything.");
}, 4000);
