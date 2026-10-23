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

"""A generic host rollout for manifest substrates.

The professional benchmarks own their own launchers (container, overlay, judge extractor).
This module is the substrate-agnostic path: stage the harness tree, copy the task's files,
hand the task prompt to the framework's CLI, capture the session stream, and snapshot every
file the run created outside the harness and the inputs.

    workspace/            the tree, as the framework reads it
    workspace/input/      task files (read-only by convention)
    output/               everything the run produced, snapshotted after the session
    session.log|.jsonl    the stream the manifest's trajectory renderer reads
    prompt.md             the exact task prompt handed to the run
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..core import runmark as RM
from ..core import tree as HT
from .manifest import GenericSubstrate

_SKIP_TOP = {"input", "output", ".git", "__pycache__", "node_modules", ".venv"}
_SKIP_ANY = {"__pycache__", ".DS_Store"}


def _rel_files(root: Path) -> list[str]:
    out: list[str] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or any(part in _SKIP_ANY for part in p.parts):
            continue
        out.append(p.relative_to(root).as_posix())
    return out


def snapshot(workspace: Path, output: Path, staged: set[str]) -> list[str]:
    """Copy every produced file into `output/`, excluding the tree and the inputs.

    Files under `workspace/output/` (the convention a framework may choose itself) are
    merged in at their own relative path rather than nested under `output/output/`.
    """
    workspace, output = Path(workspace), Path(output)
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True, exist_ok=True)
    got: list[str] = []
    for rel in _rel_files(workspace):
        if rel in staged:
            continue
        top = rel.split("/", 1)[0]
        if top in _SKIP_TOP or top.startswith("."):
            continue
        target = output / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(workspace / rel, target)
        got.append(rel)
    agent_output = workspace / "output"
    if agent_output.is_dir():
        for rel in _rel_files(agent_output):
            target = output / rel
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(agent_output / rel, target)
            got.append(rel)
    return sorted(set(got))


def run_rollout(
    *,
    adapter,
    substrate: GenericSubstrate,
    task_id: str,
    install,
    run_id: str,
    run_dir: Path,
    timeout: int | None = None,
    harness_dir: Path | None = None,
) -> Path:
    """Run one session in `run_dir`; raises when the CLI fails without producing anything."""
    run_dir = Path(run_dir)
    workspace = run_dir / "workspace"
    shutil.rmtree(workspace, ignore_errors=True)
    workspace.mkdir(parents=True, exist_ok=True)

    staged: set[str] = set()
    mode = install.extras.get("install_mode", "workspace_root")
    if mode == "split":
        src = Path(install.extras["workspace_dir"])
        staged = set(_rel_files(src))
        shutil.copytree(src, workspace, dirs_exist_ok=True)
    elif mode == "workspace_root":
        staged = set(_rel_files(Path(install.staging_dir)))
        shutil.copytree(install.staging_dir, workspace, dirs_exist_ok=True)
    home = install.extras.get("home") or None

    task_files = adapter.task_files(task_id)
    if task_files and Path(task_files).is_dir():
        shutil.copytree(task_files, workspace / "input", dirs_exist_ok=True)

    prompt = adapter.task_prompt(task_id)
    (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")

    model = str(getattr(adapter, "model", "") or "")
    rc = substrate.run_session(
        prompt=prompt,
        workspace=workspace,
        run_dir=run_dir,
        model=model,
        home=home,
        timeout=timeout or getattr(adapter, "timeout", None),
    )
    produced = snapshot(workspace, run_dir / "output", staged)
    (run_dir / "meta.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "task_id": task_id,
                "substrate": substrate.name,
                "install_mode": mode,
                "returncode": rc,
                "produced": produced,
                "harness": RM.fingerprint(harness_dir),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if rc != 0 and not produced:
        raise RuntimeError(
            f"{substrate.name} session exited {rc} and produced nothing under output/ "
            f"(run {run_id}); left unmarked so the next attempt re-rolls"
        )
    RM.mark_complete(
        run_dir,
        run_id=run_id,
        harness_dir=harness_dir,
        extra={"produced": len(produced), "returncode": rc},
    )
    return run_dir


def render_output(output: Path, *, max_chars: int = 200_000) -> str:
    """Concatenate readable produced files, naming each one."""
    output = Path(output)
    text_suffixes = {
        ".txt",
        ".md",
        ".csv",
        ".json",
        ".jsonl",
        ".py",
        ".sh",
        ".html",
        ".xml",
        ".yaml",
        ".yml",
        ".log",
        ".tsv",
        ".sql",
        ".toml",
    }
    parts: list[str] = []
    used = 0
    for rel in _rel_files(output) if output.is_dir() else []:
        p = output / rel
        if p.suffix.lower() not in text_suffixes:
            parts.append(f"=== {rel} (binary, {p.stat().st_size} bytes) ===")
            continue
        try:
            body = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        chunk = f"=== {rel} ===\n{body}"
        if used + len(chunk) > max_chars:
            chunk = chunk[: max(0, max_chars - used)]
        parts.append(chunk)
        used += len(chunk)
        if used >= max_chars:
            parts.append("…(output truncated for the proposer)")
            break
    return "\n\n".join(parts)


def tree_is_present(surface, tree_dir: Path) -> bool:
    return HT.is_tree(surface, Path(tree_dir))
