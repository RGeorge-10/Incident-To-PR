#!/usr/bin/env python3
"""Start the simulation lab: simulator + control-plane API + dashboard, on any OS.

    python scripts/sim.py            # production build of the dashboard (default)
    python scripts/sim.py --dev      # Next.js dev server with hot reload

No cluster, GitHub token or model key is needed. Everything the app sees is synthetic: generated
telemetry, a rule-based stub "model", and in-memory GitHub/Argo CD. Use it to exercise the workflow
and the UI, never as evidence of real diagnosis or repair. Press Ctrl+C to stop all three processes.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dev", action="store_true", help="run the dashboard with `next dev` instead of a production build")
    parser.add_argument("--web-port", type=int, default=3000)
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--sim-port", type=int, default=9900)
    parser.add_argument("--host", default="127.0.0.1", help="bind address for the dashboard (API and simulator stay on loopback)")
    parser.add_argument("--real-model", action="store_true", help="use LLM_MODEL / LLM_API_KEY from .env or the environment instead of the rule-based stub (telemetry stays synthetic)")
    parser.add_argument("--fresh", action="store_true", help="delete the simulation database first")
    args = parser.parse_args()

    npm = shutil.which("npm")
    if not npm:
        print("npm was not found on PATH.", file=sys.stderr)
        return 1
    database = ROOT / "data" / "simulation.sqlite3"
    if args.fresh:
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(str(database) + suffix).unlink(missing_ok=True)
            except PermissionError:
                print(f"{database.name} is in use: a previous lab run is still running. Close its terminal "
                      "(or end the stray python/node processes), then retry.", file=sys.stderr)
                return 1
    sim = f"http://127.0.0.1:{args.sim_port}"
    api = f"http://127.0.0.1:{args.api_port}"
    base = {**os.environ, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
    sim_env = {**base, "SIM_PUBLIC_URL": "/sim", "SIM_SYNC_DELAY_SECONDS": "6"}
    # Every integration is pointed at the simulator explicitly, so real credentials in the
    # environment or in .env are never used or sent anywhere while the lab is running.
    api_env = {**base, "APP_ENV": "simulation", "DATABASE_PATH": str(database),
               "PROMETHEUS_URL": f"{sim}/prom", "LOKI_URL": f"{sim}/loki", "PROMETHEUS_QUERY": "up",
               "PROMETHEUS_HEALTH_QUERY": 'kube_deployment_status_replicas_available{namespace="online-boutique",deployment="$service"}',
               "LOKI_QUERY": '{namespace="online-boutique",pod=~"$service-.*"}', "K8S_NAMESPACE": "online-boutique",
               "GITHUB_API_URL": f"{sim}/github", "GITHUB_TOKEN": "simulated-token", "GITHUB_BASE_BRANCH": "main",
               "GITHUB_CODE_REPOSITORY": "sim-lab/online-boutique", "GITHUB_GITOPS_REPOSITORY": "sim-lab/online-boutique",
               "CODE_ALLOWED_PATHS": "src/$service/", "INFRA_ALLOWED_PATHS": "deploy/online-boutique/",
               "CODE_CONTEXT_FILES": "src/$service/main.go", "INFRA_CONTEXT_FILES": "deploy/online-boutique/$service.yaml",
               "SERVICE_CONTEXT_MAP": str(ROOT / ".local" / "simulation-no-context-map.json"),
               "LLM_MODEL": "openai/rule-based-stub", "LLM_API_KEY": "simulated-key", "LLM_API_BASE": f"{sim}/llm/v1",
               "LLM_FALLBACK_MODEL": "", "ARGOCD_URL": f"{sim}/argocd", "ARGOCD_TOKEN": "simulated-token",
               "ARGOCD_APPLICATION": "online-boutique", "K8S_CONTEXT": "simulation-has-no-cluster",
               "KUBECONFIG": str(ROOT / ".local" / "simulation-no-kubeconfig"),
               "VERIFICATION_WINDOW_MINUTES": "2", "VERIFICATION_MINIMUM_SAMPLES": "3", "MONITOR_POLL_SECONDS": "5",
               "ALERTMANAGER_WEBHOOK_TOKEN": ""}
    if args.real_model:
        for key in ("LLM_MODEL", "LLM_API_KEY", "LLM_API_BASE", "LLM_FALLBACK_MODEL"):
            api_env.pop(key, None)  # fall back to the process environment, then to .env
            if key in os.environ:
                api_env[key] = os.environ[key]
        print("Using your configured model. Incident text, synthetic telemetry and synthetic source files are sent to that provider.", flush=True)
    web_env = {**base, "API_PROXY_TARGET": api, "SIM_PROXY_TARGET": sim, "NEXT_TELEMETRY_DISABLED": "1"}
    web_env.pop("NEXT_PUBLIC_API_BASE_URL", None)
    backend, frontend = str(ROOT / "backend"), str(ROOT / "frontend")
    uvicorn = [sys.executable, "-m", "uvicorn", "--app-dir", backend, "--host", "127.0.0.1"]

    if not (ROOT / "frontend" / "node_modules").is_dir():
        subprocess.run([npm, "ci"], cwd=frontend, env=web_env, check=True)
    if not args.dev:
        print("Building the dashboard…", flush=True)
        subprocess.run([npm, "run", "build"], cwd=frontend, env=web_env, check=True)
    web_command = [npm, "run", "dev" if args.dev else "start", "--", "--hostname", args.host, "--port", str(args.web_port)]

    processes = [
        subprocess.Popen([*uvicorn, "simulator.main:app", "--port", str(args.sim_port), "--log-level", "warning"], env=sim_env, cwd=backend),
        subprocess.Popen([*uvicorn, "app.main:app", "--port", str(args.api_port)], env=api_env, cwd=backend),
        subprocess.Popen(web_command, env=web_env, cwd=frontend),
    ]
    print(f"\nSimulation lab starting — dashboard: http://{args.host}:{args.web_port}  (API docs: {api}/docs)\n", flush=True)
    try:
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
        print("A process exited; stopping the lab.", file=sys.stderr)
    except KeyboardInterrupt:
        pass
    finally:
        for process in processes:
            if process.poll() is None:
                if os.name == "nt":  # npm is a .cmd shim on Windows; stop its whole process tree
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                else:
                    process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
