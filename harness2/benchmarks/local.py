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

"""The local benchmark: any directory of tasks, any registered substrate.

    <root>/base/                      the baseline harness tree (c1..c8 for that substrate)
    <root>/tasks/<domain>/<task>/     task.md, optional files/ and check.sh

A task's `check.sh` (optional) runs after adoption with OUTPUT_DIR and WORKSPACE set; exit 0
is a pass, anything else a fail. With no check script the task is unscored, never zero.

This is the substrate-agnostic path. The three professional benchmarks keep their native
runners because their artifact extraction and grading are benchmark-specific.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .. import config
from ..core import runmark as RM
from ..core import tree as HT
from ..core.spec import BenchPrompts, BenchSpec, resolve_channel_model
from ..substrate import host, registry

_LAYOUT = """local benchmark layout expected under {root}:

  base/                          the baseline harness tree for this substrate
  tasks/<domain>/<task>/task.md  the task prompt
  tasks/<domain>/<task>/files/   optional task files (copied to workspace/input/)
  tasks/<domain>/<task>/check.sh optional pass/fail check (exit 0 = pass)"""


def local_spec(root: Path, substrate) -> BenchSpec:
    channel = resolve_channel_model(None, None, effort="high")
    return BenchSpec(
        name="local",
        runs_root=Path(root) / "runs",
        install_mode=substrate.manifest.install.mode,
        prompts=BenchPrompts(
            work="complete the task described in task.md",
            deliverable_word="artifact",
        ),
        channels={"proposer": channel, "selector": channel},
        domain_word="domain",
        allow_subagents=substrate.surface().subagents_supported,
        obs_trunc=50_000,
    )


class LocalAdapter:
    """Tasks from a directory tree, executed through a manifest substrate on the host."""

    def __init__(
        self,
        root: Path | str,
        *,
        substrate: str = "dsh",
        timeout: int | None = None,
        model: str | None = None,
        split_file: Path | None = None,
        exec_root: Path | None = None,
    ):
        self.root = Path(root).expanduser().resolve()
        self.split_file = split_file
        # Rollouts live OUTSIDE the task tree: a workspace under `tasks/` would let the solver
        # walk up to `check.sh` and read the answer key. Stable hash -> resume still works.
        digest = hashlib.sha1(str(self.root).encode()).hexdigest()[:12]
        base = Path(
            exec_root
            or os.environ.get("HARNESS2_LOCAL_EXEC_DIR")
            or Path(tempfile.gettempdir()) / "harness2-local"
        )
        self.exec_root = Path(base).expanduser().resolve() / f"{self.root.name}-{digest}"
        self.substrate = registry.create(substrate)
        if not hasattr(self.substrate, "run_session"):
            raise ValueError(
                f"substrate {substrate!r} has no generic host session; the local benchmark "
                "needs a manifest substrate (dsh, hermes, qwen, workbuddy, or a JSON "
                "manifest of your own)"
            )
        self.spec = local_spec(self.root, self.substrate)
        self.model = (
            model
            or getattr(config, f"SOLVER_MODEL_{self.substrate.name.upper()}", "")
            or config.SOLVER_MODEL_OC
        )
        self.timeout = int(timeout or getattr(self.substrate.manifest.exec, "timeout", 0) or 3600)

    # -- adapter protocol ---------------------------------------------------
    def tasks(self, split: str = "all") -> list[str]:
        if not (self.root / "tasks").is_dir():
            raise FileNotFoundError(_LAYOUT.format(root=self.root))
        out = []
        for task_md in sorted((self.root / "tasks").rglob("task.md")):
            rel = task_md.parent.relative_to(self.root / "tasks").as_posix()
            out.append(rel)
        if not out:
            raise FileNotFoundError(_LAYOUT.format(root=self.root))
        return out

    def domain_of(self, task_id: str) -> str:
        return task_id.split("/", 1)[0] if "/" in task_id else "default"

    def reference_domain_order(self) -> list[str]:
        return sorted({self.domain_of(t) for t in self.tasks()})

    def task_prompt(self, task_id: str) -> str:
        p = self.root / "tasks" / task_id / "task.md"
        if not p.is_file():
            raise FileNotFoundError(f"task prompt missing: {p}")
        return p.read_text(encoding="utf-8", errors="replace")

    def task_files(self, task_id: str) -> Path | None:
        d = self.root / "tasks" / task_id / "files"
        return d if d.is_dir() else None

    def base_harness(self) -> Path:
        d = self.root / "base"
        if not HT.is_tree(self.substrate.surface(), d):
            raise FileNotFoundError(
                f"{d} is not a {self.substrate.name} harness tree "
                f"(missing one of {self.substrate.surface().always_on}).\n"
                + _LAYOUT.format(root=self.root)
            )
        return d

    def _run_dir(self, run_id: str) -> Path:
        return self.exec_root / "executions" / run_id.replace("/", "__")

    def execute(
        self, task_id: str, harness_dir: Path | None, run_id: str, *, timeout: int | None = None
    ) -> Path:
        run_dir = self._run_dir(run_id)
        hdir = Path(harness_dir) if harness_dir else self.base_harness()
        if RM.is_complete(run_dir, harness_dir=hdir) and self.artifacts_intact(run_dir):
            return run_dir
        if run_dir.exists():
            shutil.rmtree(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        install = self.substrate.materialise(hdir, self.spec, {})
        try:
            host.run_rollout(
                adapter=self,
                substrate=self.substrate,
                task_id=task_id,
                install=install,
                run_id=run_id,
                run_dir=run_dir,
                timeout=int(timeout or self.timeout),
                harness_dir=hdir,
            )
        finally:
            shutil.rmtree(install.staging_dir, ignore_errors=True)
        return run_dir

    def delivered(self, run_dir: Path) -> bool:
        return self.artifacts_intact(run_dir)

    def artifacts_intact(self, run_dir: Path) -> bool:
        out = Path(run_dir) / "output"
        return out.is_dir() and any(p.is_file() for p in out.rglob("*"))

    def trajectory(self, run_dir: Path, *, obs_trunc: int) -> str:
        path = self.substrate.trajectory_path(Path(run_dir))
        if not path.is_file():
            return f"(no trajectory for {run_dir})"
        return self.substrate.read_trajectory(path, obs_trunc=obs_trunc, max_chars=8_000_000)

    def deliverable_text(self, run_dir: Path) -> str:
        return host.render_output(Path(run_dir) / "output")

    def deliverable_files(self, run_dir: Path) -> dict[str, str]:
        out = Path(run_dir) / "output"
        got: dict[str, str] = {}
        for p in sorted(out.rglob("*")) if out.is_dir() else []:
            if not p.is_file():
                continue
            rel = p.relative_to(out).as_posix()
            try:
                got[rel] = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                got[rel] = f"(unreadable binary, {p.stat().st_size} bytes)"
        return got

    def validate_tree(self, tree: dict[str, str]) -> None:
        """No benchmark-specific invariants: the shared validator already ran."""

    def preflight(self) -> None:
        self.base_harness()
        self.substrate.preflight()

    def score(self, run_dir: Path, task_id: str) -> dict | None:
        run_dir = Path(run_dir).resolve()
        check = self.root / "tasks" / task_id / "check.sh"
        if not check.is_file():
            return None
        env = {
            **os.environ,
            "OUTPUT_DIR": str(Path(run_dir) / "output"),
            "WORKSPACE": str(Path(run_dir) / "workspace"),
            "TASK_DIR": str(self.root / "tasks" / task_id),
        }
        try:
            r = subprocess.run(
                ["bash", str(check)],
                cwd=Path(run_dir),
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "n_passed": 0,
                "n_criteria": 1,
                "all_pass": False,
                "metric": "check-error",
                "detail": f"{type(exc).__name__}: {exc}",
            }
        passed = r.returncode == 0
        detail = ((r.stdout or "") + (r.stderr or "")).strip()[-2000:]
        return {
            "n_passed": int(passed),
            "n_criteria": 1,
            "all_pass": passed,
            "metric": "check-exit",
            "detail": detail,
        }

    # -- local-specific helpers ---------------------------------------------
    def summary_paths(self) -> Path:
        return self.spec.runs_root / "adapt"

    def describe(self) -> dict:
        return json.loads(
            json.dumps(
                {
                    "root": str(self.root),
                    "substrate": self.substrate.name,
                    "install_mode": self.substrate.manifest.install.mode,
                    "tasks": len(self.tasks()),
                }
            )
        )
