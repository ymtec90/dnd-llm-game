import { existsSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const frontend = join(root, "frontend");
const apiUrl = "http://127.0.0.1:8765";
const webUrl = "http://localhost:5173";
const isWindows = process.platform === "win32";
const uvCacheDir = join(root, ".uv-cache");
const venvPython = isWindows
  ? join(root, ".venv", "Scripts", "python.exe")
  : join(root, ".venv", "bin", "python");

function canRun(cmd: string[]): boolean {
  try {
    const probe = Bun.spawnSync({
      cmd,
      stdout: "ignore",
      stderr: "ignore"
    });
    return probe.exitCode === 0;
  } catch {
    return false;
  }
}

function commandExists(command: string): boolean {
  if (isWindows) {
    if (canRun(["where", command])) return true;
  } else {
    if (canRun(["which", command])) return true;
  }

  // Fallback: manually check PATH directories
  const pathEnv = process.env.PATH || "";
  const sep = isWindows ? ";" : ":";
  const dirs = pathEnv.split(sep);
  for (const dir of dirs) {
    const filePath = join(dir, command);
    const winFilePath = join(dir, `${command}.exe`);
    try {
      if (existsSync(filePath) && statSync(filePath).isFile()) return true;
      if (isWindows && existsSync(winFilePath) && statSync(winFilePath).isFile()) return true;
    } catch {
      // ignore
    }
  }

  if (command === "bun") {
    // If this script is running under bun, bun is definitely available
    return true;
  }

  return false;
}

function findPython(): string[] {
  const candidates = isWindows ? [["py", "-3"], ["python"], ["python3"]] : [["python3"], ["python"]];
  for (const candidate of candidates) {
    if (canRun([...candidate, "--version"])) return candidate;
  }
  console.error("Python 3.11+ is required and was not found on PATH.");
  console.error("Install Python from https://www.python.org/downloads/ and enable 'Add python.exe to PATH'.");
  process.exit(1);
}

function findUv(python: string[]): string[] {
  if (canRun(["uv", "--version"])) return ["uv"];
  if (canRun([...python, "-m", "uv", "--version"])) return [...python, "-m", "uv"];
  console.log("uv was not found. Installing uv with Python...");
  runChecked([...python, "-m", "pip", "install", "uv"]);
  if (canRun([...python, "-m", "uv", "--version"])) return [...python, "-m", "uv"];
  console.error("uv installation failed. Install it manually with: python -m pip install uv");
  process.exit(1);
}

function runChecked(cmd: string[], cwd = root, env: Record<string, string> = {}) {
  const proc = Bun.spawnSync({
    cmd,
    cwd,
    env: { ...process.env, ...env },
    stdout: "inherit",
    stderr: "inherit"
  });
  if (proc.exitCode !== 0) {
    process.exit(proc.exitCode ?? 1);
  }
}

async function openBrowser(url: string) {
  await new Promise((resolve) => setTimeout(resolve, 1600));
  const candidates =
    process.platform === "darwin"
      ? [["open", url]]
      : isWindows
        ? [["cmd", "/c", "start", "", url]]
        : commandExists("powershell.exe")
          ? [["powershell.exe", "-NoProfile", "-Command", `Start-Process '${url}'`]]
          : [["xdg-open", url]];

  for (const cmd of candidates) {
    try {
      Bun.spawn({ cmd, stdout: "ignore", stderr: "ignore" });
      return;
    } catch {
      // Try the next opener.
    }
  }
}

if (!commandExists("bun")) {
  console.error("bun is required.");
  process.exit(1);
}

const python = findPython();
const uv = findUv(python);

console.log("Syncing Python environment with uv...");
runChecked([...uv, "sync", "--extra", "dev"], root, {
  UV_CACHE_DIR: uvCacheDir,
  UV_LINK_MODE: "copy"
});

console.log("Installing frontend dependencies with bun...");
runChecked(["bun", "install"], frontend);

console.log(`API: ${apiUrl}`);
console.log(`Web: ${webUrl}`);

const backendEnv = {
  ...process.env,
  PYTHONPATH: join(root, "backend")
};

const api = Bun.spawn({
  cmd: [venvPython, "-m", "dndllm26.main"],
  cwd: root,
  env: backendEnv,
  stdout: "inherit",
  stderr: "inherit"
});

const web = Bun.spawn({
  cmd: ["bun", "run", "dev", "--host", "127.0.0.1"],
  cwd: frontend,
  env: { ...process.env, VITE_API_BASE: `${apiUrl}/api` },
  stdout: "inherit",
  stderr: "inherit"
});

openBrowser(webUrl);

function shutdown() {
  api.kill();
  web.kill();
}

process.on("SIGINT", () => {
  shutdown();
  process.exit(0);
});

process.on("SIGTERM", () => {
  shutdown();
  process.exit(0);
});

await Promise.race([api.exited, web.exited]);
shutdown();
