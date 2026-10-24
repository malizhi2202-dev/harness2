"""Read a harness2 runs tree into the console's view model (read-only, no writes).

The console at `docs/product-design/prototype/app.html` normally carries demo data. This
module turns the artefacts harness2 actually writes into the same shape, so the page can show
real activity:

    <runs>/<bench>/runs/<namespace>/<task>/summary.json   demo / professional runs
    <runs>/adapt/<namespace>/<task>/summary.json          local benchmark runs
    <runs>/library/<k>/<mode>/<domain>/INDEX.md           experience library
    <runs>/executions/...                                 answer.txt / trajectory / meta.json
    <runs>/judged*.json                                   judge output (optional)

Everything missing stays missing: no score, no diff and no judged row are invented. The
frontend renders those as "未评分 / 没有保存 / 尚未判分".
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

MAX_TEXT = 60_000
MAX_TRAJ = 200_000

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv"}
_NS_RE = re.compile(r"^(?P<bench>[a-z0-9]+)-(?P<substrate>[a-z0-9]+)-(?P<mode>agg|seq)(?P<k>\d+)$")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _clip(text: str, cap: int = MAX_TEXT) -> str:
    if len(text) <= cap:
        return text
    return text[:cap] + f"\n…(+{len(text) - cap} chars truncated by the console reader)"


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().strftime("%m-%d %H:%M")


# --------------------------------------------------------------------------- discovery
def find_summaries(root: Path) -> list[tuple[Path, Path, str, str, dict]]:
    """(run_root, task_dir, namespace, task_key, summary) for every real summary.json.

    Uses the known layouts instead of walking whole trees: evidence copies under
    context/reflection/selection are never run state, and a full rglob over a runs dir with
    several 10MB snapshots costs seconds per request.
    """
    out = []
    if not root.is_dir():
        return out
    patterns = (
        "*/runs/*/*/summary.json",            # <root>/<bench>/runs/<ns>/<task>/
        "*/runs/*/*/*/summary.json",          # <root>/<bench>/runs/adapt/<ns>/<task>/
        "runs/*/*/summary.json",              # <root>/runs/<ns>/<task>/
        "runs/*/*/*/summary.json",            # <root>/runs/adapt/<ns>/<task>/
        "adapt/*/*/summary.json", "*/*/summary.json", "*/summary.json",
    )
    candidates: dict[str, Path] = {}
    for pat in patterns:
        for path in root.glob(pat):
            candidates[str(path)] = path
    for path in sorted(candidates.values()):
        parts = path.parts
        if any(p in _SKIP_DIRS for p in parts):
            continue
        if {"context", "reflection", "selection"} & set(parts):
            continue
        data = _load_json(path)
        if not isinstance(data, dict) or "task_id" not in data:
            continue
        task_dir = path.parent
        namespace = task_dir.parent.name
        # library / adapt / runs layouts: namespace is the directory that holds the task dir
        rel = path.relative_to(root)
        segs = rel.parts
        run_root = root
        if "runs" in segs:
            run_root = root / Path(*segs[: segs.index("runs")])
        out.append((run_root, task_dir, namespace, task_dir.name, data))
    return out


#: file patterns whose mtime/size decides "the console state changed"
_ARTEFACT_PATTERNS = (
    "*/runs/*/*/summary.json", "*/runs/*/*/*/summary.json",
    "runs/*/*/summary.json", "runs/*/*/*/summary.json",
    "adapt/*/*/summary.json", "*/*/summary.json", "*/summary.json",
    "*/runs/*/*/judged*.json", "runs/*/*/judged*.json",
    "*/library/*/*/*/INDEX.md", "library/*/*/*/INDEX.md",
    "*/library/*/*/*/harnesses/*/EXPERIENCE.md", "library/*/*/*/harnesses/*/EXPERIENCE.md",
    "*/runs/*/*/executions/*/*/{answer.txt,trajectory.md,meta.json}",
    "runs/*/*/executions/*/*/{answer.txt,trajectory.md,meta.json}",
)


def artefact_paths(roots: list[Path]) -> list[Path]:
    """Every file whose change can change the model. Stat only — safe to call every 2s."""
    seen: dict[str, Path] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for pat in _ARTEFACT_PATTERNS:
            for path in root.glob(pat):
                if path.is_file():
                    seen[str(path)] = path
    return sorted(seen.values(), key=str)


def signature(roots: list[Path]) -> str:
    """A cheap revision token: file identity + mtime_ns + size of every artefact."""
    import hashlib

    h = hashlib.sha1()
    for path in artefact_paths(roots):
        try:
            st = path.stat()
        except OSError:
            continue
        h.update(f"{path}\0{st.st_mtime_ns}\0{st.st_size}\n".encode())
    return h.hexdigest()[:16]


def _ns_tokens(namespace: str) -> dict:
    m = _NS_RE.match(namespace)
    if not m:
        return {"bench": namespace.split("-")[0] or "local", "substrate": "", "mode": "", "k": 0}
    return {"bench": m.group("bench"), "substrate": m.group("substrate"),
            "mode": m.group("mode"), "k": int(m.group("k"))}


def _exec_dirs(run_root: Path, namespace: str, task_id: str, label: str, summary: dict) -> list[Path]:
    """Where a rollout's answer/trajectory may live (absolute run_dir first, then layouts)."""
    cands: list[Path] = []
    for buckets in (summary.get("base") or [], list((summary.get("selections") or {}).values())):
        for item in buckets:
            if not isinstance(item, dict):
                continue
            if label not in (item.get("label"), item.get("chosen_label")):
                continue
            for key in ("run_dir", "chosen_run_dir"):
                raw = item.get(key)
                if raw:
                    cands.append(Path(raw))
    flat = task_id.replace("/", "__")
    cands += [
        run_root / "executions" / namespace / task_id / label,
        run_root / "executions" / namespace / flat / label,
        run_root / "executions" / f"{namespace}__{flat}__{label}",
        run_root / "executions" / f"{namespace}__{task_id.replace('/', '/')}" / label,
    ]
    seen, out = set(), []
    for c in cands:
        if str(c) in seen:
            continue
        seen.add(str(c))
        if c.is_dir():
            out.append(c)
    return out


def _answer(exec_dir: Path) -> str:
    for name in ("answer.txt", "output/answer.txt"):
        p = exec_dir / name
        if p.is_file():
            return _clip(_read(p))
    out = exec_dir / "output"
    if out.is_dir():
        for f in sorted(out.rglob("*")):
            if f.is_file() and f.suffix in {".txt", ".md", ".csv", ".json"}:
                return _clip(_read(f))
    return ""


def _trajectory(exec_dir: Path) -> str:
    for name in ("trajectory.md", "session.log", "session.jsonl"):
        p = exec_dir / name
        if p.is_file():
            return _clip(_read(p), MAX_TRAJ)
    return ""


# --------------------------------------------------------------------------- library
def _library(run_root: Path) -> dict:
    domains: dict[str, int] = {}
    index: dict[str, str] = {}
    entries: dict[str, list] = {}
    lib = run_root / "library"
    for index_md in sorted(lib.glob("*/*/*/INDEX.md")):
        domain = index_md.parent.name
        index[domain] = _clip(_read(index_md))
        rows = []
        for exp in sorted((index_md.parent / "harnesses").glob("*/EXPERIENCE.md")):
            body = _clip(_read(exp))
            def grab(pattern: str, default: str = "") -> str:
                m = re.search(pattern, body, re.M)
                return m.group(1).strip() if m else default
            adopted = grab(r"^- adopted:\s*`?([\w.\-]+)`?", "—")
            components = grab(r"^- components:\s*(.+)$", "—")
            why = grab(r"## Why this was chosen\s*\n+(.+?)(?:\n\s*##|\Z)")
            cue = grab(r"^- \*\*cue\*\*:\s*(.+)$", "（未填写 cue）")
            lesson = grab(r"^- \*\*lesson\*\*:\s*(.+)$", "（未填写 lesson）")
            evidence = grab(r"^- \*\*evidence\*\*:\s*(.+)$", "")
            confidence = grab(r"^- \*\*confidence\*\*:\s*(.+)$", "")
            run_ref = grab(r"run\s*`([^`]+)`")
            block = re.search(r"## What each edit did\s*\n+(.+?)(?:\n\s*##|\Z)", body, re.S)
            edits = []
            if block:
                for chunk in re.split(r"\n\s*\n", block.group(1).strip()):
                    fields = dict(re.findall(r"^(component|candidates|change|observed|verdict):\s*(.+)$",
                                             chunk, re.M))
                    if fields:
                        edits.append({
                            "component": fields.get("component", ""),
                            "candidates": fields.get("candidates", ""),
                            "change": fields.get("change", ""),
                            "observed": fields.get("observed", ""),
                            "verdict": fields.get("verdict", "—"),
                        })
            rows.append({
                "id": exp.parent.name, "adopted": adopted, "run": run_ref,
                "cue": cue, "lesson": lesson, "components": components, "why": why,
                "edits": edits, "evidence": evidence, "confidence": confidence, "body": body,
            })
        entries[domain] = rows
        domains[domain] = len(rows)
    return {
        "path": f"{run_root.name}/library",
        "index": index, "entries": entries, "domains": domains,
    }


# --------------------------------------------------------------------------- judge / report
def _judged(run_root: Path) -> dict:
    for pat in ("judged*.json", "runs/judged*.json", "runs/*/judged*.json", "reports/judged*.json"):
        for p in sorted(run_root.glob(pat)):
            data = _load_json(p)
            if isinstance(data, dict):
                return {"path": str(p), "data": data}
    return {}


# --------------------------------------------------------------------------- model
def collect(roots: list[Path], *, description: str = "", focus: str | None = None,
            task: str | None = None) -> dict:
    summaries = []
    for root in roots:
        for item in find_summaries(Path(root)):
            summaries.append((Path(root),) + item)
    if not summaries:
        raise SystemExit(
            "no harness2 runs found: pass --runs <dir> (a dir that contains runs/, adapt/ or "
            "summary.json). Example: --runs runs --runs local-bench/dsh"
        )

    # 同一个 namespace 可能在多个快照目录里重复出现：按 (namespace, task_id) 取最新的一份
    best: dict[tuple[str, str], tuple[float, tuple]] = {}
    for rec in summaries:
        key = (rec[3], str(rec[5].get("task_id")))
        mtime = rec[2].stat().st_mtime
        if key not in best or mtime > best[key][0]:
            best[key] = (mtime, rec)
    summaries = [rec for _m, rec in best.values()]

    # run选择：--focus 指定 namespace；否则按（有库 > 任务多 > 新）挑一个
    stats: dict[str, dict] = {}
    task_roots: dict[str, str] = {}
    for root, run_root, task_dir, ns, key, summary in summaries:
        mtime = task_dir.stat().st_mtime
        task_key = f"{ns}/{summary.get('task_id') or key}"
        if task_key not in task_roots:
            task_roots[task_key] = str(run_root)
        st = stats.setdefault(ns, {"ns": ns, "tasks": 0, "root": str(run_root),
                                   "has_library": (run_root / "library").is_dir(), "mtime": 0.0})
        st["tasks"] += 1
        # the namespace can appear in several snapshots; the root of its NEWEST task is the
        # one a rerun should use (--local-tasks / --output must point at that copy)
        if mtime >= st["mtime"]:
            st["root"] = str(run_root)
        st["has_library"] = st["has_library"] or (run_root / "library").is_dir()
        st["mtime"] = max(st["mtime"], mtime)
    runs_meta = sorted(stats.values(), key=lambda r: (r["has_library"], r["tasks"], r["mtime"]), reverse=True)
    fns = focus or runs_meta[0]["ns"]
    focus_records = [s for s in summaries if s[3] == fns]
    if not focus_records:
        raise SystemExit(f"run {fns!r} not found; known: {', '.join(r['ns'] for r in runs_meta)}")
    focus_records.sort(key=lambda s: s[2].stat().st_mtime, reverse=True)
    chosen = focus_records[0]
    if task:
        picked = [r for r in focus_records if str(r[5].get("task_id")) == task]
        if not picked:
            known = ", ".join(sorted({str(r[5].get("task_id")) for r in focus_records}))
            raise SystemExit(f"task {task!r} not in run {fns!r}; known: {known}")
        chosen = picked[0]
    froot, frun_root, ftask_dir, fns, ftask, fsummary = chosen
    toks = _ns_tokens(fns)
    mode = toks["mode"] or next(iter((fsummary.get("modes") or {"agg": {}}).keys()), "agg")
    modes = fsummary.get("modes") or {}
    mode_data = modes.get(mode) or next(iter(modes.values()), {})
    k = int(fsummary.get("k") or toks["k"] or 0)
    ledger = mode_data.get("ledger") or {}
    selection = (fsummary.get("selections") or {}).get(mode) or {}
    sim = mode_data.get("similarity") or {}
    stages = mode_data.get("stages") or []
    finals = mode_data.get("finals") or []
    adopted = selection.get("chosen_label") or (finals[0] if finals else "")
    labels = [b.get("label") for b in (fsummary.get("base") or []) if isinstance(b, dict)]
    def exec_for(label: str) -> Path | None:
        dirs = _exec_dirs(frun_root, fns, str(fsummary.get("task_id")), label, fsummary)
        return dirs[0] if dirs else None

    base_art = [(_answer(exec_for(lbl)) if exec_for(lbl) else "") for lbl in labels]
    final_art = _answer(exec_for(adopted)) if adopted and exec_for(adopted) else ""
    judged = _judged(froot)
    discovered = focus_records
    lib = _library(frun_root)
    events = _events(fsummary, selection, judged, frun_root)

    tasks = []
    domains: dict[str, dict] = {}
    for _root, run_root, task_dir, ns, task_key, summary in discovered:
        t_id = str(summary.get("task_id") or task_key)
        t_dom = str(summary.get("domain") or (t_id.split("/")[0] if "/" in t_id else "default"))
        t_selection = (summary.get("selections") or {})
        pick = t_selection.get(toks["mode"]) or next(iter(t_selection.values()), {})
        t_adopted = pick.get("chosen_label") or ""
        t_base = (summary.get("base") or [{}])[0]
        base_exec = _exec_dirs(run_root, ns, t_id, str(t_base.get("label") or "base1"), summary)
        t_base_text = _answer(base_exec[0]) if base_exec else ""
        t_chosen_exec = _exec_dirs(run_root, ns, t_id, t_adopted, summary) if t_adopted else []
        t_final_text = _answer(t_chosen_exec[0]) if t_chosen_exec else ""
        is_focus = task_dir == ftask_dir
        tasks.append({
            "id": t_id, "domain": t_dom, "mode": toks["mode"] or "agg",
            "k": int(summary.get("k") or k), "adopted": t_adopted or "—",
            "base": t_base_text.strip() or "—", "final": t_final_text.strip() or "—",
            "compare": (f"{t_base_text.strip()} → {t_final_text.strip()}"
                        if t_base_text.strip() and t_final_text.strip() else "—"),
            "score": "scored" if judged else ("unscored" if summary.get("status") == "ok" else "queued"),
            "detail": is_focus,
        })
        d = domains.setdefault(t_dom, {"bench": toks["bench"], "domain": t_dom, "tasks": 0,
                                       "entries": 0, "recent": "", "state": "ok"})
        d["tasks"] += 1
        d["recent"] = f"{t_id} · {_iso(task_dir.stat().st_mtime)}"
        if summary.get("status") != "ok" or summary.get("errors"):
            d["state"] = "partial"
        if t_dom in lib["domains"]:
            d["entries"] = lib["domains"][t_dom]

    # ---- focus task detail
    kpis = [
        {"k": "rollouts", "v": f"{ledger.get('R', 0)} / {ledger.get('expected_R', 0)}",
         "s": f"R = 4k · k={k}", "tone": "good" if ledger.get("R") == ledger.get("expected_R") else "bad"},
        {"k": "编辑会话", "v": f"{ledger.get('P', 0)} / {ledger.get('expected_P', 0)}",
         "s": f"P = 3k · 真实 attempts={ledger.get('session_attempts', 0)}",
         "tone": "good" if ledger.get("P") == ledger.get("expected_P") else "bad"},
        {"k": "候选 → 终版", "v": f"{len(stages)} → {len(finals)}", "s": "stages → finals"},
        {"k": "候选多样性", "v": _fmt(sim.get("mean_line_jaccard")),
         "s": f"distinct {sim.get('n_distinct', 0)}/{sim.get('n', 0)}",
         "tone": "warn" if (sim.get("n_distinct", 0) <= 1 and (sim.get("n", 0) or 0) > 1) else ""},
        {"k": "harness", "v": (selection.get("harness_fingerprint") or "—")[:16], "s": "指纹 16 位"},
        {"k": "花费", "v": f"${float(ledger.get('usd') or 0):.2f}", "s": "ledger 记账"},
    ]
    baselines = []
    expected = None
    for i, lbl in enumerate(labels):
        ed = exec_for(lbl)
        baselines.append({
            "label": lbl, "artifact": (base_art[i] or "（没有产物）").strip()[:400],
            "delivered": bool(base_art[i]),
            "trajectory": (_short_path(ed / "trajectory.md")
                           if ed and (ed / "trajectory.md").is_file() else "—"),
        })
    phases = []
    for s in stages:
        lbl = str(s.get("label"))
        verdict = ("fell back" if s.get("fell_back") else "kept base" if s.get("kept_base")
                   else "rejected" if s.get("error") else "validated")
        phases.append({"id": lbl, "dim": s.get("dim") or "—", "attempts": s.get("attempts", 1),
                       "cached": "yes" if s.get("cached") else "no", "verdict": verdict,
                       "use": "final" if lbl in finals else "probe"})
    seen_phase = {p["id"] for p in phases}
    for lbl in finals:
        if lbl in seen_phase:
            for p in phases:
                if p["id"] == lbl:
                    p["verdict"] = "adopted" if lbl == adopted else "runner-up"
                    p["use"] = "final"
            continue
        phases.append({"id": lbl, "dim": "COMPOSE", "attempts": 1, "cached": "no",
                       "verdict": "adopted" if lbl == adopted else "runner-up",
                       "use": "final"})
    rationale = [str(selection.get("why") or "（selection.json 没有写 why）")]
    for item in selection.get("dropped") or []:
        rationale.append(f"dropped: {item}")
    exp = selection.get("experience") or {}
    reflection = []
    if exp:
        reflection.append({"kind": "ok", "text": f"入库：{exp.get('cue', '')} — {exp.get('lesson', '')}"})
    if exp.get("evidence"):
        reflection.append({"kind": "ok", "text": f"依据：{exp['evidence']}"})
    if mode_data.get("fell_back"):
        reflection.append({"kind": "bad", "text": f"fell_back: {mode_data['fell_back']}"})
    for e in mode_data.get("errors") or []:
        reflection.append({"kind": "bad", "text": f"error: {e}"})
    if not reflection:
        reflection.append({"kind": "warn", "text": "该任务没有写出反思/入库条目"})
    artifacts = []
    if final_art:
        artifacts.append({"path": f"executions/{adopted}/answer.txt", "body": _clip(final_art, 4000)})
    if baselines:
        artifacts.append({"path": f"executions/{baselines[0]['label']}/answer.txt",
                          "body": (base_art[0] or "（没有产物）")[:4000]})
    run_detail = {
        "id": str(fsummary.get("task_id")), "domain": str(fsummary.get("domain") or ""),
        "mode": mode, "k": k, "status": fsummary.get("status", "ok"),
        "kpis": kpis, "baselines": baselines,
        "expected": expected, "phases": phases,
        "selection": {"adopted": adopted or "—",
                      "runnerUp": next((f for f in finals if f != adopted), "—"),
                      "blind": bool(selection.get("chosen_id")), "rationale": rationale},
        "reflection": reflection, "artifacts": artifacts,
        "rejections": [str(e) for e in mode_data.get("errors") or []],
    }

    # ---- pipeline
    def stage_card(s):
        lbl = str(s.get("label"))
        return {"label": lbl, "tone": "bad" if s.get("fell_back") or s.get("error") else
                "warn" if s.get("attempts", 1) > 1 or s.get("cached") else "ok",
                "chip": ["warn" if s.get("fell_back") or s.get("error") else "ok",
                         "fell back" if s.get("fell_back") else f"attempts {s.get('attempts', 1)}"],
                "body": (s.get("error") or s.get("notes") or "（没有 notes）")[:400],
                "meta": [f"dim {s.get('dim') or '—'}", "cached" if s.get("cached") else "not cached"],
                "notes": s.get("notes") or "", "files": [], "checks": [], "diff": []}
    pipe = {
        "taskLabel": str(fsummary.get("task_id")),
        "rolloutCount": ledger.get("R", 0),
        "base": [{"label": lbl, "tone": "ok", "chip": ["ok", "rollout"],
                  "body": "基线样本", "meta": [(base_art[i] or "（没有产物）").strip()[:60]]}
                 for i, lbl in enumerate(labels)],
        "propose": [stage_card(s) for s in stages],
        "probe": [{"label": lbl, "tone": "ok", "chip": ["ok", (final_art or "—").strip()[:30]],
                   "body": "最终候选的 rollout 产物", "meta": ["1 rollout", "弱证据：单次 rollout"]}
                  for lbl in finals],
        "compose": [{"label": lbl, "tone": "ok" if lbl == adopted else "plain",
                     "chip": ["ok" if lbl == adopted else "", "adopted" if lbl == adopted else "runner-up"],
                     "body": ("被盲选采纳的终版" if lbl == adopted else "未被采纳（runner-up）"),
                     "meta": [f"similarity {_fmt(sim.get('mean_line_jaccard'))}"],
                     "detail": True, "id": lbl} for lbl in finals],
        "composeChoices": {"kept": [adopted] if adopted else [],
                           "revised": [], "dropped": [f for f in finals if f != adopted]},
    }

    # ---- evidence (focus task)
    evidence = _evidence(frun_root, ftask_dir, fsummary, labels, stages, finals, adopted, exec_for)
    # ---- ledger
    ledger_model = _ledger_model(ledger, k)
    # ---- library model
    library_model = {
        "path": f"{frun_root.name}/library", "index": lib["index"], "entries": lib["entries"],
        "domains": [{"id": d, "label": d, "entries": n} for d, n in sorted(lib["domains"].items())],
        "gating": [["cue", "非空：情境的形状"], ["lesson", "非空：对哪个组件做什么"],
                   ["evidence", "非空：依据哪几行"], ["confidence", "可选，但建议给"]],
        "rules": [
            "库里的 harness <b>不是</b> 你正在编辑的树：要用就 copy 组件进 <span class='mono'>context/harness/</span>",
            "copy 之后<b>重新验证</b>它的 workspace fact：上一个任务的映射/阈值在这里只是假设",
            "只抄组件不抄脚本 → 校验失败（引用的脚本不存在）",
        ],
    }
    report = _report_model(judged)

    return {
        "meta": {"live": True, "roots": [str(p) for p in roots],
                 "generated_at": _iso(datetime.now().timestamp()), "description": description,
                 "focus": fns, "task_roots": task_roots,
                 "runs": [{"namespace": r["ns"], "tasks": r["tasks"],
                                         "root": r["root"], "library": r["has_library"]}
                                        for r in runs_meta]},
        "run": {
            "namespace": fns, "bench": toks["bench"], "substrate": toks["substrate"] or "—",
            "mode": mode, "k": k, "seed": fsummary.get("seed"), "tag": None,
            "improver": "见 runs 目录（真实 run）", "judge": "judged.json 存在" if judged else "无判分输出",
            "status": fsummary.get("status", "ok"), "harness": (selection.get("harness_fingerprint") or "—")[:16],
            "updated": _iso(ftask_dir.stat().st_mtime),
        },
        "domains": sorted(domains.values(), key=lambda d: (-d["tasks"], d["domain"])),
        "tasks": tasks,
        "run_detail": run_detail,
        "pipe": pipe,
        "evidence": evidence,
        "library": library_model,
        "ledger": ledger_model,
        "report": report,
        "events": events,
    }



def _short_path(path: Path) -> str:
    """Prefer a path relative to the cwd (readable in the UI), else the absolute path."""
    try:
        return str(path.resolve().relative_to(Path.cwd()))
    except ValueError:
        return str(path)

def _fmt(value) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _events(summary: dict, selection: dict, judged: dict, run_root: Path) -> list[dict]:
    out = []
    if selection.get("why"):
        out.append({"t": "select", "kind": "ok", "text": f"盲选采纳 {selection.get('chosen_label')}：{selection['why']}"})
    for mode, m in (summary.get("modes") or {}).items():
        for e in m.get("errors") or []:
            out.append({"t": mode, "kind": "bad", "text": str(e)})
        for f in m.get("fell_back") or []:
            out.append({"t": mode, "kind": "warn", "text": f"{mode}: fell_back {f}"})
    for err in summary.get("errors") or []:
        out.append({"t": "run", "kind": "bad", "text": str(err)})
    if judged:
        out.append({"t": "judge", "kind": "ok", "text": f"判分输出：{judged['path'].split('/')[-1]}"})
    else:
        out.append({"t": "judge", "kind": "warn", "text": "本次 runs 目录没有 judged.json：判分未出，未评分 ≠ 0 分"})
    if not out:
        out.append({"t": "run", "kind": "ok", "text": "没有记录到异常事件"})
    return out


def _ledger_model(ledger: dict, k: int) -> dict:
    rollouts = ledger.get("rollouts") or []
    sessions = ledger.get("sessions") or []
    rows = [["rollout", lbl, "1", "no", "0.00"] for lbl in rollouts] + \
           [["session", lbl, "0" if ledger.get("session_attempts") == 0 else "1", "no", "0.00"] for lbl in sessions]
    return {
        "kpis": [
            {"k": "R（rollouts）", "v": f"{ledger.get('R', 0)} / {ledger.get('expected_R', 0)}",
             "s": f"k={k} → 4k", "tone": "good" if ledger.get("R") == ledger.get("expected_R") else "bad"},
            {"k": "P（编辑会话）", "v": f"{ledger.get('P', 0)} / {ledger.get('expected_P', 0)}",
             "s": f"k={k} → 3k", "tone": "good" if ledger.get("P") == ledger.get("expected_P") else "bad"},
            {"k": "session attempts", "v": str(ledger.get("session_attempts", 0)), "s": "重试单列计数"},
            {"k": "缓存命中", "v": "—", "s": "ledger 没有单列该字段"},
            {"k": "usd", "v": f"${float(ledger.get('usd') or 0):.2f}", "s": "ledger 记账", "tone": "blue"},
        ],
        "rows": rows,
        "scale": [["1", "4", "3", "0", "1"], ["2", "8", "6", "1", "1"],
                  ["3", "12", "9", "1", "1"], ["4", "16", "12", "1", "1"]],
        "fingerprint": "sha256(label, mission, system, context 路径 + 每个文件的相对路径与字节)[:16]",
        "spend": [["基线 rollout", "k 次", "不可避免"], ["候选 rollout", "2k 次", "probe 的代价"],
                  ["终版 rollout", "k 次", "盲选的输入"], ["编辑会话", "3k 次", "最贵的一段"]],
    }


def _report_model(judged: dict) -> dict:
    if not judged:
        return {"empty": True,
                "emptyText": "该 runs 目录没有 judged.json：判分未出。未评分 ≠ 0 分，控制台不替它补一个数。",
                "benchmarks": [], "table": [], "discipline": [], "commands": ""}
    data = judged.get("data") or {}
    raw_rows = data.get("rows") or data.get("results") or (data if isinstance(data, list) else [])
    rows = []
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        graded = row.get("n_criteria") not in (None, 0)
        rows.append({
            "task": str(row.get("task_id") or row.get("task") or "—"),
            "label": str(row.get("label") or "—"),
            "mode": str(row.get("mode") or "—"),
            "passed": row.get("n_passed"),
            "criteria": row.get("n_criteria"),
            "all_pass": bool(row.get("all_pass")), "graded": graded,
            "error": str(row.get("error") or ""),
        })
    graded_n = sum(1 for r in rows if r["graded"])
    return {
        "empty": False, "source": judged["path"], "rows": rows,
        "graded": graded_n, "total": len(rows),
        "all_pass": sum(1 for r in rows if r["all_pass"]),
        # benchmark Δ is a harness2-report product, not something judged.json carries
        "benchmarks": [], "table": [], "discipline": [], "commands": "",
    }


def _evidence(run_root: Path, task_dir: Path, summary: dict, labels: list[str],
              stages: list[dict], finals: list[str], adopted: str, exec_for) -> dict:
    files: dict[str, dict] = {}
    tree: list[dict] = []

    def add(rel: str, body: str, label: str | None = None, kind: str = "md"):
        files[rel] = {"label": label or rel, "body": _clip(body), "kind": kind}
        return rel

    task_md = task_dir / "task.md"
    if task_md.is_file():
        add("task.md", _read(task_md), "task.md · 任务原文")
        tree.append({"name": "task.md", "icon": "i-file", "desc": "任务原文", "file": "task.md"})
    add("summary.json", _read(task_dir / "summary.json"), "summary.json · 唯一真实口径", "json")
    tree.append({"name": "summary.json", "icon": "i-file", "desc": "run 状态", "file": "summary.json"})

    src = task_dir / "source"
    if src.is_dir():
        kids = []
        for f in sorted(src.rglob("*")):
            if f.is_file():
                rel = add(f"source/{f.name}", _read(f), f"source/{f.name}")
                kids.append({"name": f.name, "icon": "i-file", "file": rel})
        if kids:
            tree.append({"name": "source/", "icon": "i-folder", "desc": f"{len(kids)} 文件",
                         "dir": True, "children": kids})

    ctx = task_dir / "context"
    if ctx.is_dir():
        kids = []
        for d in sorted(p for p in ctx.iterdir() if p.is_dir()):
            t = d / "task.md"
            if t.is_file():
                rel = add(f"context/{d.name}/task.md", _read(t), f"context/{d.name}/task.md")
                kids.append({"name": d.name, "icon": "i-file", "file": rel})
        if kids:
            tree.append({"name": "context/", "icon": "i-folder", "desc": "候选证据副本",
                         "dir": True, "children": kids})

    traj, answers = [], []
    for lbl in list(dict.fromkeys(labels + finals)):
        ed = exec_for(lbl)
        if not ed:
            continue
        ans = _answer(ed)
        if ans:
            rel = add(f"executions/{lbl}/answer.txt", ans, f"executions/{lbl}/answer.txt")
            answers.append(rel)
        tj = _trajectory(ed)
        if tj:
            rel = add(f"executions/{lbl}/trajectory.md", tj, f"executions/{lbl}/trajectory", "traj")
            traj.append(rel)
    if traj or answers:
        tree.append({"name": "executions/", "icon": "i-folder",
                     "desc": f"{len(answers)} 产物 · {len(traj)} 轨迹", "dir": True, "children": []})

    dec = []
    for d in sorted((task_dir / "selection").glob("*/selection.json")):
        rel = add(f"selection/{d.parent.name}/selection.json", _read(d), "selection.json", "json")
        dec.append(rel)
    for d in sorted((task_dir / "reflection").glob("*/edits.md")):
        rel = add(f"reflection/{d.parent.name}/edits.md", _read(d), "reflection/edits.md")
        dec.append(rel)
    if dec:
        tree.append({"name": "decisions/", "icon": "i-folder", "desc": "选择与反思",
                     "dir": True, "children": []})

    harness_dir = task_dir / "harnesses" / adopted if adopted else task_dir / "harnesses"
    harness_files = []
    if Path(harness_dir).is_dir():
        for f in sorted(Path(harness_dir).rglob("*")):
            if f.is_file() and len(harness_files) < 60:
                relp = f.relative_to(harness_dir).as_posix()
                rel = add(f"harness/{relp}", _read(f), f"harness/{relp}")
                harness_files.append(rel)
    if harness_files:
        tree.append({"name": "harness/", "icon": "i-folder", "desc": f"{len(harness_files)} 文件",
                     "dir": True, "children": []})

    tabs = [
        {"id": "traj", "label": "轨迹", "files": traj or ["summary.json"]},
        {"id": "deliv", "label": "产物", "files": answers or ["summary.json"]},
        {"id": "harness", "label": "harness 树", "files": harness_files or ["summary.json"]},
        {"id": "diff", "label": "候选 diff", "files": dec or ["summary.json"]},
        {"id": "decisions", "label": "决策", "files": dec or ["summary.json"]},
    ]
    return {
        "pathLabel": f"{task_dir.relative_to(run_root).as_posix()}",
        "tree": tree, "tabs": tabs, "files": files,
        "discipline": [
            ["不截断", "轨迹整份写入；单条观测可按 obs_trunc 截断"],
            ["无分数", "RunView 不带 score；判分在选择之后"],
            ["绊线", "7 个 grader 标记，出现在文件名或内容 → 抛异常"],
            ["答案键", "leak_globs 命中路径 → 进上下文前拒绝"],
            ["同款读取", "产物用判分器自己的 extractor 渲染"],
        ],
        "notInEvidence": ["rubric / 评分细则 / 参考答案", "pass_rate、passed_count、判分摘要",
                          "别的候选的 harness 与 run", "选择者的身份与选择理由"],
    }
