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

"""Shared plumbing for the CLIs: adapter construction and the on-disk conventions.

The conventions are defined here once, because run_sweep writes them and
judge/report read them back.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .. import config
from ..core import library as LIB
from ..core import schedule as SCHED
from ..core.spec import BenchmarkAdapter
from ..substrate import registry

BENCHES = ("jb", "wb", "lab", "local")
WB_SUBSETS = ("office", "web", "code")
SUBSTRATES = registry.choices()
NATIVE_BENCH_SUBSTRATES = ("oc", "cx")

MODES_DEFAULT = "parallel"
MODES_HELP = (
    "parallel | sequential (agg / seq accepted: the on-disk tokens); "
    "comma list, e.g. 'parallel' or 'parallel,sequential'"
)


class HelpFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    """Defaults in the help text, module docstring kept verbatim as the description."""


def canonical_substrate(name: str) -> str:
    """Resolve a substrate alias to its canonical name (raises with the known names)."""
    return registry.canonical(name)


def executor_model(substrate: str) -> str:
    """The model id recorded in a run's identity for this substrate."""
    key = registry.canonical(substrate).upper()
    return str(getattr(config, f"SOLVER_MODEL_{key}", "") or config.SOLVER_MODEL_OC)


def make_adapter(
    bench: str,
    subset: str | None,
    substrate: str,
    split_file: Path | None = None,
    local_tasks: Path | None = None,
) -> BenchmarkAdapter:
    """The one place a CLI turns (--bench, --subset, --substrate) into an adapter."""
    if bench not in BENCHES:
        raise ValueError(f"unknown bench {bench!r}; expected one of {BENCHES}")
    substrate = registry.canonical(substrate)
    if bench == "local":
        from ..benchmarks.local import LocalAdapter

        root = local_tasks or (Path(config.LOCAL_TASKS) if config.LOCAL_TASKS else None)
        if root is None:
            raise ValueError(
                "the local benchmark needs a task root: pass --local-tasks DIR or set "
                "HARNESS2_LOCAL_TASKS (see docs/SUBSTRATES.md for the layout)"
            )
        return LocalAdapter(root, substrate=substrate, split_file=split_file)
    if substrate not in NATIVE_BENCH_SUBSTRATES:
        raise ValueError(
            f"{bench} has native launchers only for {NATIVE_BENCH_SUBSTRATES}; "
            f"{substrate!r} runs through --bench local (docs/SUBSTRATES.md). "
            "Adding a native launcher for this benchmark is a per-benchmark change, not a "
            "substrate change."
        )
    if bench == "wb":
        if subset not in WB_SUBSETS:
            raise ValueError(f"--bench wb needs --subset from {WB_SUBSETS}, got {subset!r} ")
        from ..benchmarks.workbuddy import WbAdapter

        return WbAdapter(subset, substrate=substrate, split_file=split_file)
    if subset:
        raise ValueError(f"--subset applies to --bench wb only, got {subset!r} for {bench}")
    if bench == "jb":
        from ..benchmarks.jobbench import JobBenchAdapter

        return JobBenchAdapter(substrate=substrate, split_file=split_file)
    from ..benchmarks.lab import LabAdapter

    return LabAdapter(substrate=substrate, split_file=split_file)


def runs_root_for(adapter: BenchmarkAdapter) -> Path:
    """Scheduler state: summaries, harnesses and selections, not the rollout store."""
    return adapter.spec.runs_root / "adapt"


def library_root_for(adapter: BenchmarkAdapter, k: int, *, seed: int | None, tag: str = "") -> Path:
    """Per-(benchmark, substrate, k, seed, tag) experience-library root (the improver's R).

    Separate seeds and tags keep independent task streams from sharing entries.
    """
    SCHED.validate_tag(tag)
    variant = adapter.substrate.name
    if seed is not None:
        variant += f"-s{int(seed)}"
    if tag:
        variant += f"-{tag}"
    return LIB.root_for(
        adapter.spec.runs_root / "library", benchmark=adapter.spec.name, k=k, variant=variant
    )


def summary_namespace(adapter: BenchmarkAdapter, k: int, *, seed: int | None, tag: str = "") -> str:
    """The namespace whose task dirs hold summary.json for BOTH modes: run_task keeps the
    summary in the parallel mode's namespace (on-disk token "agg") whichever modes ran."""
    return SCHED.ns(adapter.spec.name, adapter.substrate.name, "agg", k, seed=seed, tag=tag)


def add_cell_args(p: argparse._ActionsContainer) -> None:
    """The (benchmark, substrate) cell selectors; `p` may be a parser or an argument group."""
    p.add_argument("--bench", required=True, choices=BENCHES, help="jb | wb | lab | local")
    p.add_argument(
        "--subset", default=None, choices=WB_SUBSETS, help="WB only: office | web | code"
    )
    p.add_argument(
        "--substrate",
        default="oc",
        choices=SUBSTRATES,
        help=f"task-executor substrate: {', '.join(SUBSTRATES)}",
    )
    p.add_argument(
        "--local-tasks",
        type=Path,
        default=None,
        help="local bench only: task root (default: $HARNESS2_LOCAL_TASKS)",
    )


def add_modes_arg(p: argparse._ActionsContainer) -> None:
    p.add_argument("--mode", "--modes", dest="modes", default=MODES_DEFAULT, help=MODES_HELP)


def add_run_identity_args(p: argparse._ActionsContainer) -> None:
    """The run-identity knobs that enter the namespace."""
    p.add_argument(
        "--k", type=int, default=1, help="scale: R=4k rollouts, P=3k improver sessions per task"
    )
    p.add_argument(
        "--seed", type=int, default=None, help="stream-order seed; omit for sorted task order"
    )
    add_modes_arg(p)


def parse_modes(raw: str) -> tuple[str, ...]:
    """'parallel,sequential' (or the on-disk tokens 'agg,seq') -> tokens in MODES order."""
    names = [s.strip() for s in raw.split(",") if s.strip()]
    if not names:
        raise ValueError(
            f"--modes takes a comma list of parallel | sequential (agg | seq), got {raw!r}"
        )
    tokens = set()
    for name in names:
        try:
            tokens.add(SCHED.mode_token(name))
        except ValueError as exc:
            raise ValueError(f"--modes: {exc} (got {raw!r})") from None
    return tuple(t for t in SCHED.MODES if t in tokens)


def mode_label(token: str) -> str:
    """Human name for an on-disk token ('agg' -> 'parallel'); unknown tokens pass through."""
    return SCHED.MODE_NAMES.get(token, token)
