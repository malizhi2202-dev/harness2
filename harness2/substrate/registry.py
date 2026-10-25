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

"""The substrate registry: one place that turns a name into a Substrate.

Built-ins are Python (opencode, codex); everything else is a JSON manifest. Adding a
framework therefore means dropping a manifest in `harness2/substrate/manifests/` or in
`HARNESS2_SUBSTRATE_DIRS` / `~/.harness2/substrates/` -- no core change, no new class.
"""

from __future__ import annotations

from typing import Callable

from .base import Launcher, Substrate
from .codex import CodexSubstrate
from .manifest import (
    GenericSubstrate,
    SubstrateManifest,
    discover_manifests,
    manifests_dir,
    user_manifest_dirs,
)
from .opencode import OpencodeSubstrate

_BUILTINS: dict[str, Callable[..., Substrate]] = {
    "oc": OpencodeSubstrate,
    "cx": CodexSubstrate,
}
_ALIASES = {
    "opencode": "oc",
    "codex": "cx",
    "hm": "hermes",
    # Manifest substrates whose product name people type differently.
    "qwen-code": "qwen",
    "qianwencode": "qwen",
    "qianwen": "qwen",
    "codebuddy": "workbuddy",
    "claude-code": "claude",
    "cbc": "workbuddy",
}
_EXTRA: dict[str, Callable[..., Substrate]] = {}


def _manifests() -> dict[str, SubstrateManifest]:
    return {m.name: m for m in discover_manifests()}


def names() -> tuple[str, ...]:
    """Canonical substrate names, built-ins first, then manifests in file order."""
    return tuple(list(_BUILTINS) + sorted(_manifests()) + sorted(_EXTRA))


def choices() -> tuple[str, ...]:
    """All accepted `--substrate` values, including aliases."""
    return tuple(sorted(set(names()) | set(_ALIASES)))


def canonical(name: str) -> str:
    key = str(name or "").strip().lower()
    key = _ALIASES.get(key, key)
    if key not in names():
        raise ValueError(
            f"unknown substrate {name!r}; known: {', '.join(names())}"
            f" (aliases: {', '.join(sorted(_ALIASES))}). "
            f"Manifests: {manifests_dir()} and "
            f"{', '.join(str(p) for p in user_manifest_dirs())}"
        )
    return key


def register(name: str, factory: Callable[..., Substrate]) -> None:
    """Register a Python substrate at runtime (tests, embedders)."""
    _EXTRA[str(name)] = factory


def manifest(name: str) -> SubstrateManifest | None:
    return _manifests().get(canonical(name))


def create(name: str, *, launcher: Launcher | None = None) -> Substrate:
    """Build the substrate for `name`; manifests get a GenericSubstrate with the launcher."""
    key = canonical(name)
    if key in _BUILTINS:
        factory = _BUILTINS[key]
        return factory(launcher=launcher) if launcher is not None else factory()
    if key in _EXTRA:
        return _EXTRA[key](launcher=launcher)
    got = _manifests().get(key)
    if got is None:  # pragma: no cover - canonical() already checked the name
        raise ValueError(f"no manifest for {key!r}")
    return GenericSubstrate(got, launcher=launcher)


def is_manifest_substrate(name: str) -> bool:
    return canonical(name) in _manifests()
