#!/usr/bin/env node
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";

const isWindows = process.platform === "win32";
const executable = path.join(
  "backend",
  ".venv",
  isWindows ? "Scripts" : "bin",
  isWindows ? "ruff.exe" : "ruff",
);

if (!existsSync(executable)) {
  console.error("backend/.venv에 Ruff가 없습니다. README에 따라 백엔드 환경을 먼저 설치하세요.");
  process.exit(1);
}

const child = spawn(executable, process.argv.slice(2), {
  stdio: "inherit",
  shell: isWindows,
});

child.on("exit", (code) => process.exit(code ?? 1));
child.on("error", (error) => {
  console.error(error.message);
  process.exit(1);
});
