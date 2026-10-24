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

"""Declarative substrates: a JSON manifest describes a harness framework's editable surface,
how a tree is installed into it, how its CLI is invoked, and how its session stream is read.

`GenericSubstrate` is the whole implementation. Adding a framework is a JSON file, not a
Python module — the shipped manifests (`dsh`, `hermes`, `qwen`, `workbuddy`) are themselves
values here.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from ..core import runmark as RM
from ..core import tree as HT
from ..core.tree import SurfaceSpec
from .base import Install, Launcher, RunRequest

INSTALL_MODES = ("workspace_root", "home_dir", "split")
TRAJECTORY_FORMATS = ("text", "jsonl")
HOOKS_SCHEMAS = ("claude", "none", "generic")
SKILL_NAME_RULES = ("must_match", "overrides_dir")
PLACEHOLDERS = ("workspace", "prompt", "model", "home", "bin")
_REQUIRED_SURFACE = ("always_on", "always_on_max", "skill_dir")
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


class ManifestError(ValueError):
    """A substrate manifest is missing a field or names an unsupported value."""


@dataclass(frozen=True)
class InstallSpec:
    """How a tree becomes files the harness framework reads."""

    mode: str = "workspace_root"
    root: str = ""
    entries: tuple[str, ...] = ()
    home_entries: tuple[str, ...] = ()
    # Copy a few non-editable files (config/auth) from an existing home into the staged one.
    seed_env: str = ""
    seed_default: str = ""
    seed_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecSpec:
    """How to start one non-interactive session of the framework."""

    argv: tuple[str, ...]
    stdin: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    bin_env: str = ""
    default_bin: str = ""
    timeout: int = 3600


@dataclass(frozen=True)
class TrajectorySpec:
    """Where the session stream lands and how to render it into the shared grammar."""

    format: str = "text"
    path: str = "session.log"


@dataclass(frozen=True)
class SubstrateManifest:
    name: str
    label: str
    surface: SurfaceSpec
    install: InstallSpec
    exec: ExecSpec
    trajectory: TrajectorySpec


def _need(obj: Mapping, key: str, where: str):
    if key not in obj:
        raise ManifestError(f"{where}: missing required field {key!r}")
    return obj[key]


def _surface(manifest_name: str, d: Mapping) -> SurfaceSpec:
    where = f"{manifest_name}.surface"
    for key in _REQUIRED_SURFACE:
        _need(d, key, where)
    rule = str(d.get("skill_name_rule", "must_match"))
    if rule not in SKILL_NAME_RULES:
        raise ManifestError(
            f"{where}.skill_name_rule: expected one of {SKILL_NAME_RULES}, got {rule!r}"
        )
    schema = str(d.get("hooks_schema", "none"))
    if schema not in HOOKS_SCHEMAS:
        raise ManifestError(
            f"{where}.hooks_schema: expected one of {HOOKS_SCHEMAS}, got {schema!r}"
        )
    hooks_file = d.get("hooks_file")
    events = frozenset(str(x) for x in d.get("hook_events", ()))
    if hooks_file and schema == "claude" and not events:
        raise ManifestError(f"{where}: a claude hooks_schema needs hook_events")
    stubs = d.get("stubs", ())
    try:
        pairs = tuple((str(s["path"]), str(s["body"])) for s in stubs)
    except (TypeError, KeyError) as exc:
        raise ManifestError(f"{where}.stubs: expected [{{'path','body'}}, ...]") from exc
    return SurfaceSpec(
        name=manifest_name,
        always_on=tuple(str(x) for x in d["always_on"]),
        always_on_max=int(d["always_on_max"]),
        always_on_cap_reason=str(
            d.get("always_on_cap_reason", "the always-on budget is paid on every turn")
        ),
        skill_dir=str(d["skill_dir"]),
        skill_name_rule=rule,
        trigger_hint=str(d.get("trigger_hint", "name the skill in the always-on text")),
        autotrigger_file=str(d.get("autotrigger_file") or d["always_on"][0]),
        trigger_line=str(d.get("trigger_line", "- When {desc}, load the `{name}` skill.\n")),
        subagent_requires_mode=bool(d.get("subagent_requires_mode", True)),
        plugin_dir=d.get("plugin_dir"),
        hooks_file=hooks_file,
        hook_events=events,
        compile_skill_scripts=bool(d.get("compile_skill_scripts", False)),
        reject_empty_always_on=bool(d.get("reject_empty_always_on", False)),
        stubs=pairs,
        subagent_dir=str(d.get("subagent_dir", "sub_agents")),
        hooks_schema=schema,
        prompt_md=str(d.get("prompt_md", "")),
        skills_auto_advertised=bool(d.get("skills_auto_advertised", False)),
        subagents_supported=bool(d.get("subagents_supported", True)),
    )


def _install(d: Mapping, where: str) -> InstallSpec:
    mode = str(d.get("mode", "workspace_root"))
    if mode not in INSTALL_MODES:
        raise ManifestError(f"{where}: install.mode must be one of {INSTALL_MODES}, got {mode!r}")
    root = str(d.get("root", ""))
    if mode in ("home_dir", "split") and not root:
        raise ManifestError(f"{where}: install.mode={mode!r} needs a non-empty `root`")
    entries = tuple(str(x) for x in d.get("entries", ()))
    home_entries = tuple(str(x) for x in d.get("home_entries", ()))
    bad = [e for e in entries + home_entries if e.startswith("/") or ".." in e.split("/")]
    if bad:
        raise ManifestError(
            f"{where}: install.entries/home_entries must be relative and stay inside the tree: {bad}"
        )
    if mode == "split" and not home_entries:
        raise ManifestError(f"{where}: install.mode='split' needs a non-empty `home_entries`")
    seed_files = tuple(str(x) for x in d.get("seed_files", ()))
    bad_seed = [x for x in seed_files if x.startswith("/") or ".." in x.split("/")]
    if bad_seed:
        raise ManifestError(f"{where}: seed_files must be relative: {bad_seed}")
    return InstallSpec(
        mode=mode,
        root=root,
        entries=entries,
        home_entries=home_entries,
        seed_env=str(d.get("seed_env", "")),
        seed_default=str(d.get("seed_default", "")),
        seed_files=seed_files,
    )


def _template(value: str, where: str) -> str:
    for name in _PLACEHOLDER.findall(value):
        if name not in PLACEHOLDERS:
            raise ManifestError(
                f"{where}: unknown placeholder {{{name}}}; known: {', '.join(PLACEHOLDERS)}"
            )
    return value


def _exec(d: Mapping, where: str) -> ExecSpec:
    argv = tuple(str(x) for x in d.get("argv", ()))
    if not argv:
        raise ManifestError(f"{where}: exec.argv must be a non-empty list")
    for i, part in enumerate(argv):
        _template(part, f"{where}.exec.argv[{i}]")
    stdin = d.get("stdin")
    if stdin is not None:
        _template(str(stdin), f"{where}.exec.stdin")
    env = {
        str(k): _template(str(v), f"{where}.exec.env.{k}") for k, v in (d.get("env") or {}).items()
    }
    has_prompt = any("{prompt}" in p for p in argv)
    if stdin is None and not has_prompt:
        raise ManifestError(
            f"{where}: exec must deliver the prompt -- put {{prompt}} in argv or set exec.stdin"
        )
    return ExecSpec(
        argv=argv,
        stdin=None if stdin is None else str(stdin),
        env=env,
        bin_env=str(d.get("bin_env", "")),
        default_bin=str(d.get("default_bin", "")),
        timeout=int(d.get("timeout", 3600)),
    )


def _trajectory(d: Mapping, where: str) -> TrajectorySpec:
    fmt = str(d.get("format", "text"))
    if fmt not in TRAJECTORY_FORMATS:
        raise ManifestError(
            f"{where}: trajectory.format must be one of {TRAJECTORY_FORMATS}, got {fmt!r}"
        )
    path = str(d.get("path", "session.log"))
    if path.startswith("/") or ".." in path.split("/"):
        raise ManifestError(f"{where}: trajectory.path must stay inside the run directory")
    return TrajectorySpec(format=fmt, path=path)


def load_manifest(path: Path | str) -> SubstrateManifest:
    """Read and validate one JSON manifest; every error names the offending field."""
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read substrate manifest {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError(f"{path}: expected a JSON object")
    name = str(_need(raw, "name", str(path)))
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ManifestError(f"{path}: name must be lowercase letters, digits or '_' (got {name!r})")
    return SubstrateManifest(
        name=name,
        label=str(raw.get("label", name)),
        surface=_surface(name, raw.get("surface") or {}),
        install=_install(raw.get("install") or {}, f"{path}.install"),
        exec=_exec(raw.get("exec") or {}, str(path)),
        trajectory=_trajectory(raw.get("trajectory") or {}, f"{path}.trajectory"),
    )


class GenericSubstrate:
    """A Substrate whose surface, install, exec and trajectory are data."""

    def __init__(
        self,
        manifest: SubstrateManifest,
        *,
        launcher: Launcher | None = None,
        home: Path | str | None = None,
    ):
        self.manifest = manifest
        self._launcher = launcher
        self.home = Path(home) if home else None

    # -- Substrate protocol -------------------------------------------------
    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def label(self) -> str:
        return self.manifest.label

    def surface(self) -> SurfaceSpec:
        return self.manifest.surface

    def materialise(self, tree_dir: Path, spec, ctx: Mapping[str, str]) -> Install:
        """Stage the tree the way this framework reads it.

        `workspace_root` copies the named entries (or the whole tree) to the staging root;
        `home_dir` copies them under `<home>/<install.root>`. `ctx` entries keyed `file:<rel>`
        are written on top, so a benchmark can inject integration files it owns.
        """
        tree_dir = Path(tree_dir)
        if not HT.is_tree(self.surface(), tree_dir):
            raise ValueError(
                f"{tree_dir} is not a {self.name} harness tree (no always-on file present)"
            )
        staged = Path(tempfile.mkdtemp(prefix=f"adapt-{self.name}-"))
        inst = self.manifest.install
        try:
            if inst.mode == "split":
                dest = staged / "workspace"
                dest.mkdir(parents=True, exist_ok=True)
                home_base = self.home or staged / "home"
                home_dir = home_base / inst.root
                home_dir.mkdir(parents=True, exist_ok=True)
            else:
                dest = staged
                if inst.mode == "home_dir":
                    dest = (self.home or staged / "home") / inst.root
                    dest.mkdir(parents=True, exist_ok=True)
                home_dir = dest
            entries = inst.entries or (
                tuple(p.name for p in sorted(tree_dir.iterdir())) if inst.mode != "split" else ()
            )
            for entry in entries:
                src = tree_dir / entry
                if not src.exists():
                    continue
                tgt = dest / entry
                tgt.parent.mkdir(parents=True, exist_ok=True)
                if src.is_dir():
                    shutil.copytree(src, tgt)
                else:
                    shutil.copy2(src, tgt)
            for entry in inst.home_entries:
                src = tree_dir / entry
                if not src.exists():
                    continue
                tgt = home_dir / entry
                tgt.parent.mkdir(parents=True, exist_ok=True)
                if src.is_dir():
                    shutil.copytree(src, tgt)
                else:
                    shutil.copy2(src, tgt)
            if inst.seed_files:
                src_home = os.environ.get(inst.seed_env, "")
                if not src_home and inst.seed_default:
                    src_home = inst.seed_default
                src_home = Path(os.path.expanduser(src_home)) if src_home else None
                if src_home and src_home.is_dir():
                    for rel in inst.seed_files:
                        src = src_home / rel
                        if src.is_file():
                            tgt = home_dir / rel
                            tgt.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src, tgt)
            for key, body in ctx.items():
                if not key.startswith("file:"):
                    continue
                rel = HT.normalize_rel(key[len("file:") :])
                p = dest / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(body, encoding="utf-8")
        except BaseException:
            shutil.rmtree(staged, ignore_errors=True)
            raise
        return Install(
            staging_dir=staged,
            fingerprint=RM.fingerprint(tree_dir),
            extras={
                "install_mode": inst.mode,
                "home": str(
                    (self.home or staged / "home") / inst.root
                    if inst.mode in ("home_dir", "split")
                    else (self.home or "")
                ),
                **({"workspace_dir": str(staged / "workspace")} if inst.mode == "split" else {}),
            },
        )

    def execute(
        self, task_ref: str, install: Install, run_ids: list[str], timeout: int
    ) -> list[Path]:
        if self._launcher is None:
            raise RuntimeError(
                f"{self.name} substrate was constructed without a launcher body; "
                "a benchmark adapter must supply one (or use the local benchmark)"
            )
        return self._launcher(
            RunRequest(task_ref=task_ref, run_ids=tuple(run_ids), timeout=int(timeout)), install
        )

    def read_trajectory(self, stream_path: Path, *, obs_trunc: int, max_chars: int) -> str:
        if self.manifest.trajectory.format == "jsonl":
            return render_jsonl(Path(stream_path), obs_trunc=obs_trunc, max_chars=max_chars)
        try:
            text = Path(stream_path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"(failed to read trajectory: {exc})"
        text = text.replace("\x00", "")
        if len(text) > max_chars:
            return text[:max_chars] + f"\n…(+{len(text) - max_chars} chars — render truncation)"
        return text or "(empty trajectory)"

    def preflight(self) -> None:
        """Fail before money is spent when the CLI is not installed."""
        spec = self.manifest.exec
        binary = self._binary()
        if Path(binary).is_file():
            return
        if shutil.which(binary):
            return
        where = f" Set {spec.bin_env} to its path." if spec.bin_env else ""
        raise RuntimeError(
            f"{self.name} executor not found: {binary!r}.{where} Manifest: {self.manifest.label}."
        )

    # -- host-runner helpers ------------------------------------------------
    def trajectory_path(self, run_dir: Path) -> Path:
        return Path(run_dir) / self.manifest.trajectory.path

    def _binary(self) -> str:
        spec = self.manifest.exec
        if spec.bin_env:
            configured = os.environ.get(spec.bin_env, "").strip()
            if configured:
                return configured
        return spec.default_bin or self.manifest.name

    def run_argv(
        self, *, prompt: str, workspace: Path, model: str = "", home: Path | str | None = None
    ) -> tuple[list[str], str | None]:
        """Expand the manifest's argv/stdin template; returns (argv, stdin)."""
        ctx = {
            "workspace": str(workspace),
            "prompt": prompt,
            "model": model,
            "home": str(home or ""),
            "bin": self._binary(),
        }
        argv = [part.format(**ctx) for part in self.manifest.exec.argv]
        stdin = None if self.manifest.exec.stdin is None else self.manifest.exec.stdin.format(**ctx)
        return argv, stdin

    def run_env(
        self,
        base: Mapping[str, str],
        *,
        workspace: Path,
        home: Path | str | None = None,
        model: str = "",
    ) -> dict[str, str]:
        env = dict(base)
        ctx = {"workspace": str(workspace), "home": str(home or ""), "model": model}
        for key, value in self.manifest.exec.env.items():
            env[key] = value.format(**ctx)
        if home:
            env.setdefault("HOME", str(home))
        return env

    def run_session(
        self,
        *,
        prompt: str,
        workspace: Path,
        run_dir: Path,
        model: str = "",
        home: Path | str | None = None,
        timeout: int | None = None,
    ) -> int:
        """Run one non-interactive session, capturing its stream where `trajectory.path` says."""
        argv, stdin = self.run_argv(prompt=prompt, workspace=workspace, model=model, home=home)
        env = self.run_env(os.environ, workspace=workspace, home=home, model=model)
        wall = int(timeout or self.manifest.exec.timeout or 3600)
        stdin_kw = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
        proc = subprocess.run(
            argv,
            cwd=workspace,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=wall,
            **stdin_kw,
        )
        target = self.trajectory_path(run_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.manifest.trajectory.format == "jsonl":
            target.write_text(proc.stdout or "", encoding="utf-8")
        else:
            target.write_text(
                (proc.stdout or "") + ("\n[stderr]\n" + proc.stderr if proc.stderr else ""),
                encoding="utf-8",
            )
        (target.parent / "stderr.log").write_text(proc.stderr or "", encoding="utf-8")
        return int(proc.returncode)


def render_jsonl(path: Path, *, obs_trunc: int, max_chars: int) -> str:
    """Render a tolerant JSONL agent stream into [ACTION]/[OBSERVATION]/[ASSISTANT].

    Frameworks disagree on field names, so this reads the common shapes instead of one
    schema: an event's `type`/`kind`/`event` decides the role, and the payload is taken from
    `item`, `payload`, `data`, or the event itself. Unknown shapes degrade to [HARNESS].
    """
    lines: list[str] = []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"(failed to read trajectory: {exc})"
    for line in raw.replace("\x00", "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        body = _body(ev)
        kind = str(ev.get("type") or ev.get("kind") or ev.get("event") or body.get("type") or "")
        k = re.sub(r"[_/.]", "-", kind.lower())
        text = _text(body)
        role = str(body.get("role") or ev.get("role") or "").lower()
        if role == "user" or k in ("user-message", "user"):
            if text.lstrip().startswith(("# AGENTS.md", "Instructions from:", "# Instructions")):
                lines.append(f"[HARNESS AGENTS.md] {_clip(text, obs_trunc)}")
            else:
                lines.append(f"[USER] {_clip(text, obs_trunc)}")
            continue
        if any(
            x in k for x in ("tool-call", "function-call", "tool-use", "command-execution", "shell")
        ):
            target = (
                body.get("command")
                or body.get("cmd")
                or body.get("arguments")
                or body.get("input")
                or text
            )
            lines.append(f"[ACTION] {_clip(target, obs_trunc)}")
        elif any(
            x in k
            for x in (
                "tool-result",
                "function-call-output",
                "tool-response",
                "observation",
                "execution-output",
            )
        ):
            out = body.get("output") or body.get("result") or body.get("content") or text
            lines.append(f"[OBSERVATION] {_clip(out, obs_trunc)}")
        elif any(x in k for x in ("agent-message", "assistant", "message")) and (
            "user" not in k or text
        ):
            lines.append(f"[ASSISTANT] {_clip(text, obs_trunc)}")
        elif any(x in k for x in ("reasoning", "thinking")):
            if text.strip():
                lines.append(f"[REASONING] {_clip(text, obs_trunc)}")
        elif "error" in k or "failed" in k:
            lines.append(f"[ERROR] {_clip(text, obs_trunc)}")
        elif text.strip():
            lines.append(f"[HARNESS] {kind or 'event'}: {_clip(text, obs_trunc)}")
    rendered = "\n".join(lines).strip()
    if not rendered:
        return "(no parseable events in trajectory)"
    if len(rendered) > max_chars:
        rendered = (
            rendered[:max_chars] + f"\n…(+{len(rendered) - max_chars} chars — render truncation)"
        )
    return rendered


def _body(ev: dict) -> dict:
    for key in ("item", "payload", "data"):
        v = ev.get(key)
        if isinstance(v, dict):
            return v
    return ev


def _text(obj) -> str:
    """Best-effort text from a message-ish payload (str, text, content, message)."""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        for key in ("text", "message", "content", "summary", "delta"):
            v = obj.get(key)
            if isinstance(v, str) and v.strip():
                return v
            if isinstance(v, (list, dict)):
                got = _text(v)
                if got.strip():
                    return got
        return ""
    if isinstance(obj, list):
        return "\n".join(t for t in (_text(x) for x in obj) if t.strip())
    return ""


def _clip(value, n: int) -> str:
    s = "" if value is None else str(value)
    s = s.replace("\x00", "")
    if n <= 0 or len(s) <= n:
        return s
    return s[:n] + f"…(+{len(s) - n} chars — render truncation)"


def manifests_dir() -> Path:
    """Manifests shipped inside the package."""
    return Path(__file__).resolve().parent / "manifests"


def user_manifest_dirs() -> list[Path]:
    """Extra manifest directories: `HARNESS2_SUBSTRATE_DIRS` (colon list) + `~/.harness2/substrates`."""
    out: list[Path] = []
    raw = os.environ.get("HARNESS2_SUBSTRATE_DIRS", "")
    out.extend(Path(p).expanduser() for p in raw.split(os.pathsep) if p.strip())
    home = Path(os.environ.get("HARNESS2_HOME", "~/.harness2")).expanduser()
    out.append(home / "substrates")
    return out


def discover_manifests() -> list[SubstrateManifest]:
    """All manifests on disk, user dirs last so they can override a shipped name."""
    seen: dict[str, SubstrateManifest] = {}
    for directory in (manifests_dir(), *user_manifest_dirs()):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            manifest = load_manifest(path)
            seen[manifest.name] = manifest
    return list(seen.values())
