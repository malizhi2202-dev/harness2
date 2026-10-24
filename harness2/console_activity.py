"""What is harness2 doing right now? Inferred from artefacts on disk and /proc.

harness2 is a CLI, not a daemon: a run is a sequence of file writes (execution dirs,
`prompt.md`, `session.log`, `answer.txt`, `harnesses/`, `selection/`, `summary.json`). There is
no status file to read, so this module reports what can be defended:

* **processes** – live `harness2*` commands (pid / cmdline / cwd / elapsed);
* **events** – artefact writes inside a time window, newest first, labelled by what wrote them;
* **active** – per (run, task) the phase implied by which artefacts exist, plus the last write.

Everything is a heuristic and says so: a task is "at probe" because executions for candidate
labels exist and its summary has not been written yet — not because harness2 told us.
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".pytest_cache", "workspace"}
#: evidence copies / harness trees / inputs are immutable per rollout; walking them would
#: dominate the scan without telling us anything new (their parent dir is the event).
PRUNE_DIRS = {"context", "harness", "harnesses", "harness_workspace", "base", "sources",
              "tool_descriptions", "skills", "scripts", "sub_agents", "plugin", "deliverables"}
#: these are the dirs whose creation IS the event, so they are recorded before pruning
STAGE_DIRS = {"harnesses": "harness", "selection": "select", "reflection": "reflect",
              "context": "context"}
MAX_FILES = 60_000
_LABEL = re.compile(r"^(base|inv|dom|agg|c)\d+$")
_TASK_LIKE = re.compile(r"^[a-z0-9][a-z0-9_.\-]*(/[a-z0-9][a-z0-9_.\-]*)?$")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().strftime("%m-%d %H:%M:%S")


def _root_for(path: Path, roots: list[Path]) -> Path:
    best = roots[0]
    for root in roots:
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if len(str(root)) > len(str(best)):
            best = root
    return best


def _split_exec(parts: list[str]) -> tuple[str, str, str]:
    """(namespace, task, label) from the parts after an `executions/` segment."""
    if not parts:
        return "", "", ""
    if "__" in parts[0]:
        bits = parts[0].split("__")
        return bits[0], "/".join(bits[1:-1]) or (bits[1] if len(bits) > 1 else ""), bits[-1]
    ns = parts[0]
    label = ""
    for i, part in enumerate(parts[1:], start=1):
        if _LABEL.match(part):
            label = part
            task = "/".join(parts[1:i])
            return ns, task, label
    return ns, "/".join(parts[1:]), label


def _run_task(path: Path, root: Path) -> tuple[str, str]:
    """Best-effort (namespace, task) for an artefact path; ("", "") when it is not task state."""
    rel = path.relative_to(root).as_posix()
    parts = rel.split("/")
    if "executions" in parts:
        ns, task, _label = _split_exec(parts[parts.index("executions") + 1:])
        return ns, task
    if "runs" in parts:
        after = parts[parts.index("runs") + 1:]
        if after and after[0] == "adapt":
            after = after[1:]
        if not after:
            return "", ""
        ns = after[0]
        if "__" in ns and "library" not in parts:
            bits = ns.split("__")
            return bits[0], "/".join(bits[1:])
        # skip the run subtree when the root already points at a namespace dir
        tail = [p for p in after[1:] if _TASK_LIKE.match(p) and not p.endswith((".json", ".md"))]
        task = tail[0] if tail else ""
        if task.startswith("summary") or task.startswith("task"):
            task = after[1] if len(after) > 1 else ""
        return ns, task
    if "library" in parts:
        i = parts.index("library")
        rest = parts[i + 1:]
        domain = rest[2] if len(rest) > 2 else ""
        return "library", domain
    return "", ""


def _classify(rel: str) -> tuple[str, str]:
    """(kind, label) — what kind of work wrote this file."""
    name = rel.rsplit("/", 1)[-1]
    if name == "summary.json":
        return "summary", "任务 summary 更新"
    if name == "summary.lock":
        return "lock", "summary 写入中（lock 存在）"
    if name.startswith("judged") and name.endswith(".json"):
        return "judge", "判分输出"
    if name == "prompt.md":
        return "start", "任务提示写入（rollout 开始）"
    if name in ("session.log", "session.jsonl", "trajectory.md"):
        return "trajectory", "轨迹写入"
    if "/output/" in rel or name in ("answer.txt",):
        return "artifact", "产物写入"
    if name == "meta.json":
        return "meta", "rollout 结束（meta 写入）"
    if name == ".adapt_complete":
        return "meta", "rollout 标记完成"
    if "/harnesses/" in rel:
        return "harness", "候选 harness 生成"
    if "/selection/" in rel:
        return "select", "盲选会话"
    if "/reflection/" in rel:
        return "reflect", "反思 / 采纳"
    if "/context/" in rel:
        return "context", "证据快照生成"
    if (rel.startswith("library/") or "/library/" in rel) and name in ("INDEX.md", "EXPERIENCE.md"):
        return "library", "经验库写入"
    if name in ("task.md", "MISSION.md"):
        return "task", "任务输入"
    return "other", name


def _phase_for(task_dir: Path) -> str:
    """Ordered by how far a task got; the summary is the terminal marker."""
    summary, lock = task_dir / "summary.json", task_dir / "summary.lock"
    if lock.is_file() and (not summary.is_file() or lock.stat().st_mtime >= summary.stat().st_mtime):
        return "正在写 summary"
    if summary.is_file():
        return "已出 summary"
    if (task_dir / "reflection").is_dir() and any((task_dir / "reflection").iterdir()):
        return "反思 / 采纳"
    if (task_dir / "selection").is_dir() and any((task_dir / "selection").iterdir()):
        return "盲选"
    if (task_dir / "harnesses").is_dir() and any((task_dir / "harnesses").iterdir()):
        return "合成 / probe"
    if (task_dir / "context").is_dir() and any((task_dir / "context").iterdir()):
        return "probe"
    if (task_dir / "base").is_dir():
        return "基线"
    return "排队 / 只写了输入"


def _processes() -> list[dict]:
    out = []
    me = os.getpid()
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == me:
            continue
        pid = int(entry)
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            continue
        cmd = " ".join(p.decode("utf-8", "replace") for p in raw.split(b"\0") if p).strip()
        first = cmd.split()[0] if cmd.split() else ""
        exe_name = first.rsplit("/", 1)[-1]
        if not ("harness2." in cmd or exe_name.startswith("harness2")):
            continue
        exe = ""
        try:
            exe = os.readlink(f"/proc/{pid}/exe")
        except OSError:
            pass
        if exe.rsplit("/", 1)[-1] in ("bash", "sh", "dash", "zsh") and "python" not in cmd:
            continue  # the wrapper shell; its python child is the run
        if "harness2.cli.console" in cmd or "harness2-console" in cmd:
            continue  # the console itself is not a run
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
            started_ticks = float(stat.rsplit(")", 1)[1].split()[19])
            btime = 0.0
            for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines():
                if line.startswith("btime "):
                    btime = float(line.split()[1])
            elapsed = max(0.0, time.time() - (btime + started_ticks / os.sysconf("SC_CLK_TCK")))
            state = stat.rsplit(")", 1)[1].split()[0]
        except (OSError, IndexError, ValueError):
            elapsed, state = 0.0, "?"
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            cwd = ""
        out.append({"pid": pid, "cmd": cmd[:220], "cwd": cwd, "elapsed_s": int(elapsed),
                    "state": state})
    return sorted(out, key=lambda p: -p["elapsed_s"])


def collect(roots: list[Path], *, minutes: int = 120, limit: int = 120) -> dict:
    now = time.time()
    window = max(1, int(minutes)) * 60
    events: list[dict] = []
    active: dict[tuple[str, str], dict] = {}
    scanned = 0

    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            here = Path(dirpath)
            if here.name in STAGE_DIRS:
                try:
                    st = here.stat()
                except OSError:
                    st = None
                if st and now - st.st_mtime <= window:
                    run, task = _run_task(here, root)
                    if run:
                        kind = STAGE_DIRS[here.name]
                        events.append({"t": _iso(st.st_mtime), "age_s": int(max(0, now - st.st_mtime)),
                                       "kind": kind, "label": {"harness": "候选 harness 目录生成",
                                                               "select": "盲选目录生成",
                                                               "reflect": "反思目录生成",
                                                               "context": "证据快照目录生成"}[kind],
                                       "run": run, "task": task,
                                       "path": str(here.relative_to(root)) + "/", "size": 0})
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and d not in PRUNE_DIRS]
            if scanned > MAX_FILES:
                break
            for name in filenames:
                path = Path(dirpath) / name
                scanned += 1
                try:
                    st = path.stat()
                except OSError:
                    continue
                if now - st.st_mtime > window:
                    continue
                run, task = _run_task(path, root)
                if not run:
                    continue
                kind, label = _classify(path.relative_to(root).as_posix())
                events.append({
                    "t": _iso(st.st_mtime), "age_s": int(max(0, now - st.st_mtime)),
                    "kind": kind, "label": label, "run": run, "task": task,
                    "path": str(path.relative_to(root)), "size": st.st_size,
                })
                key = (run, task)
                prev = active.get(key)
                if prev is None or st.st_mtime > prev["_mtime"]:
                    active[key] = {"run": run, "task": task, "last_write": _iso(st.st_mtime),
                                   "age_s": int(max(0, now - st.st_mtime)),
                                   "last_kind": kind, "last_label": label, "_mtime": st.st_mtime}

    # phase per task from the task dir (only when it is on disk)
    for key, item in active.items():
        run, task = key
        candidates = [root for root in roots if root.is_dir()]
        phase = ""
        for root in candidates:
            for pat in (f"*/runs/{run}/{task}", f"runs/{run}/{task}", f"runs/adapt/{run}/{task}",
                        f"adapt/{run}/{task}", f"*/runs/adapt/{run}/{task}"):
                for task_dir in root.glob(pat):
                    if task_dir.is_dir():
                        phase = _phase_for(task_dir)
                        break
                if phase:
                    break
            if phase:
                break
        item["phase"] = phase or "只看到执行目录（未到阶段目录）"
        item.pop("_mtime", None)

    events.sort(key=lambda e: e["age_s"])
    total_events = len(events)
    procs = _processes()
    return {
        "generated_at": _iso(now), "window_minutes": max(1, int(minutes)),
        "scanned_files": scanned, "processes": procs,
        "active": sorted(active.values(), key=lambda a: a["age_s"]),
        "events_total": total_events,
        "events": events[: max(1, int(limit))],
        "running": bool(procs),
    }
