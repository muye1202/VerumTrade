from __future__ import annotations

import os
import platform
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path


BACKEND_PORT = 8000
FRONTEND_PORT = 5173
MIN_PYTHON = (3, 10)
MIN_NODE = (18, 0, 0)
MIN_NPM = (9, 0, 0)
LLM_API_KEYS = (
    "OPENAI_API_KEY",
    "AZURE_FOUNDRY_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENROUTER_API_KEY",
    "DASHSCOPE_API_KEY",
    "ZHIPUAI_API_KEY",
)


@dataclass
class Issue:
    message: str
    fix: str


@dataclass
class PreflightResult:
    python_exec: str
    npm_exec: str
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def repo_root() -> Path:
    return Path(__file__).resolve().parent


def is_windows() -> bool:
    return platform.system() == "Windows"


def command_hint(*parts: str) -> str:
    return " ".join(parts)


def frontend_install_hint(root: Path) -> str:
    package_lock = root / "frontend" / "package-lock.json"
    install_command = "npm ci" if package_lock.exists() else "npm install"
    return f"cd frontend\n{install_command}"


def find_python_executable(root: Path) -> str:
    venv_python = (
        root / "venv" / "Scripts" / "python.exe"
        if is_windows()
        else root / "venv" / "bin" / "python"
    )
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def command_version(command: str, args: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            [command, *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None

    output = (completed.stdout or completed.stderr).strip()
    return output.splitlines()[0].strip() if output else None


def parse_version(value: str | None) -> tuple[int, ...] | None:
    if not value:
        return None
    match = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", value)
    if not match:
        return None
    return tuple(int(part) for part in match.groups(default="0"))


def version_at_least(found: tuple[int, ...] | None, minimum: tuple[int, ...]) -> bool:
    if found is None:
        return False
    width = max(len(found), len(minimum))
    return found + (0,) * (width - len(found)) >= minimum + (0,) * (width - len(minimum))


def python_version(python_exec: str) -> tuple[int, ...] | None:
    output = command_version(
        python_exec,
        ["-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
    )
    return parse_version(output)


def can_import_backend(python_exec: str) -> tuple[bool, str]:
    script = (
        "import importlib.util, sys; "
        "missing=[m for m in ('uvicorn','fastapi','api.main') if importlib.util.find_spec(m) is None]; "
        "print(', '.join(missing)); "
        "sys.exit(1 if missing else 0)"
    )
    try:
        completed = subprocess.run(
            [python_exec, "-c", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
            cwd=str(repo_root()),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)

    detail = (completed.stdout or completed.stderr).strip()
    return completed.returncode == 0, detail


def is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value:
            values[key] = value
    return values


def has_llm_key(env_values: dict[str, str]) -> bool:
    return any(os.environ.get(key) or env_values.get(key) for key in LLM_API_KEYS)


def run_preflight(root: Path) -> PreflightResult:
    python_exec = find_python_executable(root)
    npm_exec = "npm.cmd" if is_windows() else "npm"
    result = PreflightResult(python_exec=python_exec, npm_exec=npm_exec)

    if not (root / "venv").exists():
        result.warnings.append(
            Issue(
                "No local virtual environment was found at ./venv; using the current Python interpreter.",
                "For an isolated setup, run: python -m venv venv\n"
                + (
                    "venv\\Scripts\\activate"
                    if is_windows()
                    else "source venv/bin/activate"
                )
                + "\npython -m pip install -e .",
            )
        )

    py_version = python_version(python_exec)
    if not version_at_least(py_version, MIN_PYTHON):
        found = ".".join(map(str, py_version)) if py_version else "not found"
        result.errors.append(
            Issue(
                f"Python {found} is selected, but Verumtrade needs Python 3.10 or newer.",
                "Install Python 3.10+ and rerun: python run.py",
            )
        )

    backend_ok, backend_detail = can_import_backend(python_exec)
    if not backend_ok:
        detail = f" Missing modules: {backend_detail}." if backend_detail else ""
        result.errors.append(
            Issue(
                "Backend Python dependencies are not ready." + detail,
                "From the project root, run: python -m pip install -e .",
            )
        )

    node_version = parse_version(command_version("node", ["--version"]))
    if not version_at_least(node_version, MIN_NODE):
        found = ".".join(map(str, node_version)) if node_version else "not found"
        result.errors.append(
            Issue(
                f"Node.js {found} is selected, but Verumtrade needs Node.js 18 or newer.",
                "Install Node.js 18+ from https://nodejs.org/ and rerun: python run.py",
            )
        )

    npm_version = parse_version(command_version(npm_exec, ["--version"]))
    if not version_at_least(npm_version, MIN_NPM):
        found = ".".join(map(str, npm_version)) if npm_version else "not found"
        result.errors.append(
            Issue(
                f"npm {found} is selected, but Verumtrade needs npm 9 or newer.",
                "Install a current Node.js LTS release, then rerun: python run.py",
            )
        )

    frontend = root / "frontend"
    if not (frontend / "package.json").exists():
        result.errors.append(
            Issue(
                "The frontend/package.json file was not found.",
                "Run this launcher from the Verumtrade project root.",
            )
        )
    elif not (frontend / "node_modules").exists():
        result.errors.append(
            Issue(
                "Frontend dependencies are not installed.",
                frontend_install_hint(root),
            )
        )

    env_file = root / ".env"
    env_values = parse_env_file(env_file)
    if not env_file.exists():
        copy_command = (
            "copy .env.example .env"
            if is_windows()
            else "cp .env.example .env"
        )
        result.warnings.append(
            Issue(
                ".env was not found.",
                f"Create one from the template: {copy_command}",
            )
        )

    if not has_llm_key(env_values):
        result.warnings.append(
            Issue(
                "No LLM provider API key was found in .env or the current shell environment.",
                "Add at least one provider key such as OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY, DEEPSEEK_API_KEY, OPENROUTER_API_KEY, DASHSCOPE_API_KEY, or ZHIPUAI_API_KEY.",
            )
        )

    for port, service in ((BACKEND_PORT, "backend"), (FRONTEND_PORT, "frontend")):
        if is_port_open("127.0.0.1", port):
            result.errors.append(
                Issue(
                    f"Port {port} is already in use, so the {service} cannot start there.",
                    f"Stop the process using port {port}, then rerun: python run.py",
                )
            )

    return result


def print_banner() -> None:
    print("=" * 58)
    print("  Verumtrade local launcher")
    print("=" * 58)


def print_issues(title: str, issues: list[Issue]) -> None:
    if not issues:
        return
    print(f"\n{title}")
    for index, issue in enumerate(issues, start=1):
        print(f"  {index}. {issue.message}")
        print("     Fix:")
        for line in issue.fix.splitlines():
            print(f"       {line}")


def terminate_process(process: subprocess.Popen | None) -> None:
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def launch(root: Path, preflight: PreflightResult) -> int:
    backend_process: subprocess.Popen | None = None
    frontend_process: subprocess.Popen | None = None

    try:
        backend_cmd = [
            preflight.python_exec,
            "-m",
            "uvicorn",
            "api.main:app",
            "--reload",
            "--host",
            "127.0.0.1",
            "--port",
            str(BACKEND_PORT),
        ]
        frontend_cmd = [preflight.npm_exec, "run", "dev", "--", "--host", "127.0.0.1"]

        print("\nStarting backend: " + command_hint(*backend_cmd))
        backend_process = subprocess.Popen(backend_cmd, cwd=str(root))

        time.sleep(1)

        print("Starting frontend: " + command_hint(*frontend_cmd))
        frontend_process = subprocess.Popen(frontend_cmd, cwd=str(root / "frontend"))

        print("\nVerumtrade is starting.")
        print(f"  Frontend: http://localhost:{FRONTEND_PORT}")
        print(f"  Backend:  http://localhost:{BACKEND_PORT}")
        print("Press Ctrl+C to stop both processes.\n")

        while True:
            backend_code = backend_process.poll()
            frontend_code = frontend_process.poll()
            if backend_code is not None:
                terminate_process(frontend_process)
                return backend_code
            if frontend_code is not None:
                terminate_process(backend_process)
                return frontend_code
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nShutting down Verumtrade...")
        return 0
    finally:
        terminate_process(frontend_process)
        terminate_process(backend_process)


def main() -> int:
    root = repo_root()
    print_banner()

    preflight = run_preflight(root)
    print_issues("Warnings:", preflight.warnings)

    if not preflight.ok:
        print_issues("Cannot start yet:", preflight.errors)
        print("\nAfter fixing the items above, run: python run.py")
        return 1

    return launch(root, preflight)


if __name__ == "__main__":
    sys.exit(main())
