#!/usr/bin/env node
/*
 * 역할: 프론트엔드(vite)와 백엔드(uvicorn)를 한 번에 띄우는 개발용 실행 스크립트.
 * 입력: 없음. `npm run dev`로 실행한다.
 * 출력: 두 프로세스의 stdout/stderr를 그대로 흘려보내고, 하나가 죽으면 함께 종료한다.
 *
 * 백엔드는 반드시 backend/ 를 작업 디렉터리로 실행한다 — Settings(app/config.py)가
 * .env를 상대경로로 찾기 때문에, 저장소 루트에서 띄우면 backend/.env를 읽지 못하고
 * 오류 없이 전 Provider가 fake로 뜬다. 실행 위치와 무관하게 같은 경로를 쓰도록
 * 이 파일 기준 절대경로로 계산한다.
 */
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const isWindows = process.platform === "win32";
const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const backendDir = resolve(repoRoot, "backend");
const frontendDir = resolve(repoRoot, "frontend");

// .env가 없으면 조용히 fake Provider로 뜨므로, 시작 전에 눈에 띄게 알려준다.
if (!existsSync(resolve(backendDir, ".env"))) {
  console.warn(
    "[BACKEND] backend/.env가 없습니다. .env.example을 복사해 채우지 않으면 " +
      "모든 Provider가 fake 모드로 실행됩니다.",
  );
}

const commands = [
  {
    name: "FRONTEND",
    command: "npm",
    args: ["run", "dev"],
    cwd: frontendDir,
  },
  {
    name: "BACKEND",
    command: "python",
    args: ["-m", "uvicorn", "app.main:app", "--reload", "--port", "8000"],
    cwd: backendDir,
  },
];

const children = commands.map(({ name, command, args, cwd }) => {
  const child = spawn(command, args, {
    stdio: "inherit",
    shell: isWindows,
    cwd,
  });

  child.on("exit", (code) => {
    if (shuttingDown) return;
    console.error(`${name} exited with code ${code ?? 1}`);
    shutdown(code ?? 1);
  });

  child.on("error", (error) => {
    console.error(`${name} failed to start: ${error.message}`);
    shutdown(1);
  });

  return child;
});

let shuttingDown = false;

function shutdown(code) {
  shuttingDown = true;
  for (const child of children) {
    if (!child.killed) child.kill();
  }
  process.exit(code);
}

process.on("SIGINT", () => shutdown(0));
process.on("SIGTERM", () => shutdown(0));
