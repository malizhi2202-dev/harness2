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

"""Solver substrates behind one interface (substrate/base.py).

`OpencodeSubstrate` (oc) and `CodexSubstrate` (cx) are Python classes with native
benchmark launchers. Everything else is a JSON manifest turned into a `GenericSubstrate`
(dsh, hermes, or a framework you add); `registry` resolves names, `host` runs a generic
workspace rollout, and `manifest` owns the schema.
"""

from .base import Install, Launcher, RunRequest, Substrate
from .codex import CODEX_SURFACE, CodexSubstrate
from .manifest import GenericSubstrate, SubstrateManifest, discover_manifests, load_manifest
from .opencode import OPENCODE_SURFACE, OpencodeSubstrate
from .registry import canonical, choices, create, names, register

__all__ = [
    "Install",
    "Launcher",
    "RunRequest",
    "Substrate",
    "OpencodeSubstrate",
    "OPENCODE_SURFACE",
    "CodexSubstrate",
    "CODEX_SURFACE",
    "GenericSubstrate",
    "SubstrateManifest",
    "discover_manifests",
    "load_manifest",
    "canonical",
    "choices",
    "create",
    "names",
    "register",
]
