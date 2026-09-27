#!/usr/bin/env node
"use strict";

const { spawn, execSync } = require("child_process");
const path = require("path");
const fs = require("fs");
const os = require("os");
const http = require("http");
const net = require("net");

const HOME = os.homedir();
const STATE_DIR = path.join(HOME, ".docs-mcp");
const VENV_DIR = path.join(STATE_DIR, "venv");
const SRC_DIR = path.join(STATE_DIR, "src");
const MARKER = path.join(VENV_DIR, ".setup-done");
const API_PORT = 8000;
const PG_PORT = 5432;

const DOCKER_COMPOSE = `services:
  postgres:
    image: pgvector/pgvector:pg16
    container_name: docs-mcp-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: docs_mcp
      POSTGRES_PASSWORD: docs_mcp
      POSTGRES_DB: docs_mcp
    ports:
      - "127.0.0.1:5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U docs_mcp -d docs_mcp"]
      interval: 5s
      timeout: 5s
      retries: 12

volumes:
  pgdata:
`;

function log(msg) { process.stderr.write(`docs-mcp: ${msg}\n`); }

function findPython() {
  const candidates = ["python3.14", "python3.13", "python3.12", "python3"];
  for (const bin of candidates) {
    try {
      const out = execSync(`${bin} -c "import sys; print(sys.version_info[:2])"`, { encoding: "utf8" }).trim();
      const [major, minor] = out.replace(/[()]/g, "").split(",").map(Number);
      if (major >= 3 && minor >= 12) return bin;
    } catch {}
  }
  return null;
}

function copySource() {
  const bundled = path.join(__dirname, "..", "python");
  fs.mkdirSync(SRC_DIR, { recursive: true });
  execSync(`cp -a "${bundled}/." "${SRC_DIR}/"`, { stdio: "inherit" });
}

function createVenv(python) {
  fs.mkdirSync(STATE_DIR, { recursive: true });
  log("creating virtual environment...");
  execSync(`${python} -m venv "${VENV_DIR}"`, { stdio: "inherit" });
}

function installDeps() {
  const pip = path.join(VENV_DIR, "bin", "pip");
  log("installing dependencies... this may take a few minutes on first run");
  execSync(`${pip} install --quiet --upgrade pip`, { stdio: "inherit" });
  execSync(`${pip} install --quiet "${SRC_DIR}[local]"`, { stdio: "inherit" });
  const playwright = path.join(VENV_DIR, "bin", "playwright");
  if (fs.existsSync(playwright)) {
    log("installing chromium for web crawler...");
    execSync(`${playwright} install --with-deps chromium 2>/dev/null || true`, { stdio: "inherit" });
  }
  fs.writeFileSync(MARKER, new Date().toISOString());
  log("setup complete");
}

function ensureSetup() {
  if (fs.existsSync(MARKER)) return;
  const python = findPython();
  if (!python) {
    log("error: Python 3.12+ not found. Install Python and try again.");
    log("  https://www.python.org/downloads/");
    process.exit(1);
  }
  log(`first-time setup — using ${python}`);
  createVenv(python);
  copySource();
  installDeps();
}

function ensureConfig() {
  const envFile = path.join(STATE_DIR, ".env");
  if (!fs.existsSync(envFile)) {
    fs.mkdirSync(STATE_DIR, { recursive: true });
    fs.writeFileSync(envFile, [
      "# docs-mcp configuration",
      "# https://github.com/yourname/docs-mcp#configuration",
      "",
      "LLM_API_KEY=your-key-here",
      "LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai",
      "LLM_MODEL=gemini-3.6-flash",
      "DATABASE_URL=postgresql://docs_mcp:docs_mcp@localhost:5432/docs_mcp",
      "",
      "# Local embedding model (requires GPU for best performance)",
      "# local_embedding_model=sentence-transformers/all-MiniLM-L6-v2",
    ].join("\n"));
    log(`created config template at ${envFile}`);
    log("edit this file with your API keys before starting the server");
  }
}

function isPortInUse(port) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    socket.setTimeout(2000);
    socket.on("connect", () => { socket.destroy(); resolve(true); });
    socket.on("timeout", () => { socket.destroy(); resolve(false); });
    socket.on("error", () => resolve(false));
    socket.connect(port, "127.0.0.1");
  });
}

function ensureDockerCompose() {
  const composePath = path.join(STATE_DIR, "docker-compose.yml");
  if (!fs.existsSync(composePath)) {
    fs.mkdirSync(STATE_DIR, { recursive: true });
    fs.writeFileSync(composePath, DOCKER_COMPOSE);
  }
  return composePath;
}

function hasDocker() {
  try {
    execSync("docker --version", { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

async function ensurePostgres() {
  if (await isPortInUse(PG_PORT)) {
    log("postgres already running");
    return;
  }
  if (!hasDocker()) {
    log("postgres not running and docker not found");
    log("start postgres manually or install docker: https://docs.docker.com/get-docker/");
    return;
  }
  const composePath = ensureDockerCompose();
  log("starting postgres via docker...");
  try {
    execSync(`docker compose -f "${composePath}" up -d`, { stdio: "ignore" });
  } catch {
    log("docker compose failed — is docker running?");
    return;
  }
  const start = Date.now();
  while (Date.now() - start < 30000) {
    if (await isPortInUse(PG_PORT)) {
      log("postgres ready");
      return;
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  log("postgres failed to start within 30s");
}

function startApiServer(python) {
  const envFile = path.join(STATE_DIR, ".env");
  const child = spawn(python, ["-m", "docs_mcp.api"], {
    cwd: SRC_DIR,
    stdio: "ignore",
    env: { ...process.env },
    detached: true,
  });
  child.unref();
  log(`api server started on port ${API_PORT} (pid ${child.pid})`);
  return child;
}

function waitForApi(timeoutMs = 30000) {
  return new Promise((resolve) => {
    const start = Date.now();
    const check = async () => {
      if (await isPortInUse(API_PORT)) return resolve(true);
      if (Date.now() - start > timeoutMs) return resolve(false);
      setTimeout(check, 1000);
    };
    check();
  });
}

async function maybeStartApi(python) {
  if (await isPortInUse(API_PORT)) {
    log(`api server already running on port ${API_PORT}`);
    return;
  }
  startApiServer(python);
  const ready = await waitForApi();
  if (ready) {
    log(`api server ready at http://127.0.0.1:${API_PORT}`);
  } else {
    log("api server failed to start (is postgres running?)");
  }
}

async function run() {
  ensureSetup();
  ensureConfig();
  const python = path.join(VENV_DIR, "bin", "python");
  const useApi = process.argv.includes("--api");
  if (!useApi) {
    await ensurePostgres();
    await maybeStartApi(python);
  }
  const mod = useApi ? "docs_mcp.api" : "docs_mcp.server";
  const child = spawn(python, ["-m", mod], {
    cwd: SRC_DIR,
    stdio: ["inherit", "inherit", "inherit"],
    env: { ...process.env },
  });
  child.on("exit", (code) => process.exit(code ?? 1));
  process.on("SIGINT", () => child.kill("SIGINT"));
  process.on("SIGTERM", () => child.kill("SIGTERM"));
}

run();
