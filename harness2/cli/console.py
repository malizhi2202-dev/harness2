# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Serve the product console over REAL harness2 runs (read-only).

The console page (`docs/product-design/prototype/app.html`) ships with demo data so it can be
opened from disk. This command makes it show what is actually on disk:

    harness2-console --runs runs --runs local-bench/dsh
    # -> http://127.0.0.1:8099/            the console, reading /api/state
    # -> http://127.0.0.1:8099/api/state   the model the page renders
    # -> http://127.0.0.1:8099/api/events  SSE: a revision whenever an artefact changes
    # -> http://127.0.0.1:8099/api/activity  what harness2 is doing right now

Two modes, one data path:

* **serve** (default) reads the runs tree on every `/api/state`, so a refresh shows a run that
  just finished. Bound to 127.0.0.1 by default and strictly read-only.
* **--export FILE** bakes the same model into a standalone HTML copy, for sharing a snapshot
  without running a server.

There is no write path and no arbitrary file endpoint: the model carries the text it needs.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import config
from ..console_activity import collect as collect_activity
from ..console_data import collect, signature

APP_HTML = config.PKG.parent / "docs" / "product-design" / "prototype" / "app.html"


def _model(runs: list[Path], focus: str | None, task: str | None = None) -> dict:
    model = collect(runs, focus=focus, task=task)
    meta = model.setdefault("meta", {})
    meta["revision"] = signature(runs)
    meta["read_at"] = time.strftime("%H:%M:%S")
    return model


def _export(runs: list[Path], focus: str | None, out: Path) -> Path:
    if not APP_HTML.is_file():
        raise SystemExit(f"console page not found: {APP_HTML}")
    model = _model(runs, focus)
    payload = json.dumps(model, ensure_ascii=False).replace("</", "<\\/")
    injected = f"<script>window.__H2_DATA__ = {payload};</script>\n</head>"
    html = APP_HTML.read_text(encoding="utf-8").replace("</head>", injected, 1)
    out = out.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def _config(runs: list[Path], host: str, port: int, allow_run: bool = False) -> dict:
    """Everything the settings page shows: server, data sources, tools, env (masked)."""
    exes = ("harness2", "harness2-judge", "harness2-report", "harness2-console",
            "dsh", "hermes", "qwen", "claude", "codebuddy", "opencode", "codex")
    bin_env = {"claude": "HARNESS2_CLAUDE_BIN", "qwen": "HARNESS2_QWEN_BIN",
               "dsh": "HARNESS2_DSH_BIN", "hermes": "HARNESS2_HERMES_BIN",
               "codebuddy": "HARNESS2_WORKBUDDY_BIN"}
    env = {}
    for key, value in sorted(os.environ.items()):
        if key.startswith("HARNESS2_"):
            env[key] = value if "KEY" not in key and "TOKEN" not in key else "***"
    return {
        "server": {
            "command": "harness2-console", "python": sys.version.split()[0],
            "bind": host or "127.0.0.1", "port": port,
            "page": str(APP_HTML), "page_exists": APP_HTML.is_file(),
            "read_only": True, "allow_run": bool(allow_run),
        },
        "roots": [{"path": str(r), "exists": r.is_dir()} for r in runs],
        "executables": {name: (os.environ.get(bin_env.get(name, ""), "").strip()
                               or shutil.which(name) or "") for name in exes},
        "env": env,
        "endpoints": [
            ["GET /", "控制台页面"],
            ["GET /api/state", "整份模型（每次读盘；ETag/304）"],
            ["GET /api/state?task=", "指定任务作为 run_detail"],
            ["GET /api/activity", "此刻在写什么（进程 + 最近写入 + 阶段）"],
            ["GET /api/events", "SSE：产物变化时推 revision"],
            ["GET /api/config", "本页：服务端/数据源/工具/环境"],
            ["GET /api/rerun?run=&task=", "生成复跑命令"],
            ["POST /api/rerun", "在本机执行复跑（需 --allow-run）"],
            ["GET /healthz", "存活探针"],
            ["POST *", "405（拒绝写）"],
        ],
    }


def rerun_plan(runs: list[Path], run: str, task: str) -> dict:
    """The exact reproduction command for one task, plus whether this server may spawn it.

    harness2 has no `--only-tasks`, so the plan scopes by domain: the same namespace, k and
    mode are reused, and `--max-tasks` caps the stream (cached rollouts are free).
    """
    if not run or not task:
        raise ValueError("run and task are required")
    model = collect(runs, focus=run, task=task)
    root = (model["meta"].get("task_roots") or {}).get(f"{run}/{task}") or next(
        (r["root"] for r in model["meta"]["runs"] if r["namespace"] == run), "")
    meta = model["run"]
    detail = model["run_detail"]
    bench = meta.get("bench") or "local"
    substrate = meta.get("substrate") or "oc"
    k = int(meta.get("k") or 1)
    parallel = "parallel" if (meta.get("mode") or "agg") == "agg" else "sequential"
    domain = detail.get("domain") or (task.split("/")[0] if "/" in task else "default")
    if bench == "demo":
        command = f"harness2-demo --output {root or 'runs/demo'} --k {k} --mode both"
        note = "demo 是确定性链路：同一命令会重放整条 run。"
    elif bench in ("jb", "lab", "wb"):
        extra = " --subset web" if bench == "wb" else ""
        command = (f"harness2 --bench {bench}{extra} --k {k} --mode {parallel} "
                   f"--only-domains {domain} --max-tasks 1")
        note = f"按 domain 复跑（{domain} 的第一个任务）；同名 run 会续跑命中缓存。"
    else:
        command = (f"harness2 --bench local --substrate {substrate} --k {k} --mode {parallel} "
                   f"--local-tasks {root or '.'} --only-domains {domain} --max-tasks 1")
        note = f"按 domain 复跑（{domain} 的第一个任务）；--local-tasks 指向这次 run 的任务根。"
    return {"run": run, "task": task, "root": root, "command": command, "note": note,
            "allow_run": False, "cwd": str(config.PKG.parent)}


def spawn_rerun(command: str, runs: list[Path]) -> dict:
    """Run a plan locally (only when --allow-run was passed). Returns pid + log path."""
    cwd = config.PKG.parent
    base = Path(runs[0]).expanduser().resolve().parent if runs else cwd
    log_dir = base / ".logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"console-rerun-{time.strftime('%Y%m%d-%H%M%S')}.log"
    handle = open(log_path, "ab")
    proc = subprocess.Popen(shlex.split(command), cwd=str(cwd), stdout=handle, stderr=handle,
                            start_new_session=True)
    return {"pid": proc.pid, "log": str(log_path), "command": command}


def _handler(runs: list[Path], allow_run: bool = False):
    class Handler(BaseHTTPRequestHandler):
        server_version = "harness2-console"

        def log_message(self, fmt: str, *args) -> None:  # one line per request
            sys.stderr.write("harness2-console: " + (fmt % args) + "\n")

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: dict, extra: dict | None = None) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8", extra)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            url = urlparse(self.path)
            if url.path in ("/", "/index.html", "/app.html"):
                if not APP_HTML.is_file():
                    self._json(500, {"error": f"console page missing: {APP_HTML}"})
                    return
                self._send(200, APP_HTML.read_bytes(), "text/html; charset=utf-8")
                return
            if url.path == "/api/state":
                # Cheap ETag first: an unchanged runs tree answers 304 without rebuilding.
                try:
                    etag = signature(runs)
                except OSError as exc:
                    self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
                    return
                if self.headers.get("If-None-Match", "").strip('"') == etag:
                    self._send(304, b"", "application/json; charset=utf-8", {"ETag": f'"{etag}"'})
                    return
                query = parse_qs(url.query)
                focus = (query.get("focus") or [None])[0]
                task = (query.get("task") or [None])[0]
                try:
                    model = _model(runs, focus or None, task or None)
                except SystemExit as exc:  # no runs found
                    self._json(404, {"error": str(exc)})
                except Exception as exc:  # noqa: BLE001 - surface the real error to the page
                    self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
                else:
                    self._json(200, model, {"ETag": f'"{etag}"'})
                return
            if url.path == "/api/config":
                host, port = self.server.server_address[:2]
                self._json(200, _config(runs, str(host), int(port), allow_run))
                return
            if url.path == "/api/rerun":
                query = parse_qs(url.query)
                try:
                    plan = rerun_plan(runs, (query.get("run") or [""])[0],
                                      (query.get("task") or [""])[0])
                except SystemExit as exc:
                    self._json(404, {"error": str(exc)})
                except ValueError as exc:
                    self._json(400, {"error": str(exc)})
                except Exception as exc:  # noqa: BLE001
                    self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
                else:
                    plan["allow_run"] = bool(allow_run)
                    self._json(200, plan)
                return
            if url.path == "/api/activity":
                query = parse_qs(url.query)
                try:
                    minutes = int((query.get("minutes") or ["120"])[0])
                    limit = int((query.get("limit") or ["120"])[0])
                except ValueError:
                    self._json(400, {"error": "minutes/limit must be integers"})
                    return
                try:
                    self._json(200, collect_activity(runs, minutes=minutes, limit=limit))
                except Exception as exc:  # noqa: BLE001 - surface the real error to the page
                    self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
                return
            if url.path == "/api/events":
                self._events()
                return
            if url.path == "/healthz":
                self._json(200, {"ok": True})
                return
            self._json(404, {"error": "not found; try /, /api/state or /api/events"})

        def do_POST(self) -> None:  # noqa: N802 - the only write-ish action is a guarded rerun
            if self.path.split("?")[0] != "/api/rerun":
                self._json(405, {"error": "read-only console"})
                return
            if not allow_run:
                self._json(403, {"error": "rerun is disabled; start harness2-console with --allow-run"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                plan = rerun_plan(runs, str(body.get("run") or ""), str(body.get("task") or ""))
                result = spawn_rerun(plan["command"], runs)
            except SystemExit as exc:
                self._json(404, {"error": str(exc)})
            except (ValueError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
            else:
                self._json(202, {**result, "run": plan["run"], "task": plan["task"]})

        def _events(self) -> None:
            """Server-sent events: a revision event whenever the artefacts change."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                last = signature(runs)
                self.wfile.write(self._sse("ready", {"revision": last}))
                self.wfile.flush()
                idle = 0.0
                while True:
                    time.sleep(2.0)
                    current = signature(runs)
                    if current != last:
                        last = current
                        idle = 0.0
                        self.wfile.write(self._sse("change", {"revision": last}))
                    else:
                        idle += 2.0
                        if idle >= 10.0:
                            idle = 0.0
                            self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

        @staticmethod
        def _sse(event: str, data: dict) -> bytes:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()

    return Handler



def _lan_addresses() -> list[str]:
    """Best-effort IPv4 addresses of this host, for the startup banner."""
    found: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 9))  # TEST-NET-1, never actually sent
        found.add(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    return sorted(a for a in found if not a.startswith("127."))

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="harness2-console",
        description="Serve the Harness² console over real runs (read-only).",
        epilog="Run roots may be given repeatedly: --runs runs --runs local-bench/dsh",
    )
    parser.add_argument("--runs", action="append", default=None, metavar="DIR",
                        help="a directory that contains runs/, adapt/ or summary.json "
                             "(default: ./runs; repeatable)")
    parser.add_argument("--focus", default=None, metavar="NAMESPACE",
                        help="namespace to focus (default: the newest run that has a library)")
    parser.add_argument("--bind", default="127.0.0.1", help="interface to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8099, help="port (default: 8099)")
    parser.add_argument("--export", default=None, metavar="HTML",
                        help="write a standalone HTML snapshot instead of serving")
    parser.add_argument("--open", action="store_true", help="open the browser after binding")
    parser.add_argument("--allow-run", action="store_true",
                        help="allow POST /api/rerun to spawn the generated harness2 command "
                             "(default off; the server only builds and shows commands)")
    args = parser.parse_args(argv)

    runs = [Path(r).expanduser() for r in (args.runs or ["runs"])]
    for root in runs:
        if not root.exists():
            print(f"warning: run root does not exist: {root}", file=sys.stderr)

    if args.export:
        out = _export(runs, args.focus, Path(args.export))
        print(f"wrote {out} (snapshot of {', '.join(str(r) for r in runs)})")
        return 0

    try:
        _model(runs, args.focus)  # fail fast with a readable message
    except SystemExit as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    httpd = ThreadingHTTPServer((args.bind, args.port), _handler(runs, args.allow_run))
    host, port = httpd.server_address[0], httpd.server_address[1]
    url = f"http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}/"
    print(f"harness2-console serving {', '.join(str(r) for r in runs)}")
    print(f"  console: {url}")
    print(f"  data:    {url}api/state")
    if host in ("0.0.0.0", "::"):
        for lan in _lan_addresses():
            print(f"  LAN:     http://{lan}:{port}/")
        print("  note:    bound to every interface, no auth — read-only, but only expose it on a"
              " trusted network")
    if args.open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
