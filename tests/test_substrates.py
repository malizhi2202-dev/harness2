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

"""Substrate manifests, generic installs, the local benchmark, and the JSONL renderer."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from harness2.benchmarks.local import LocalAdapter
from harness2.core import schedule
from harness2.core import tree as HT
from harness2.core.proposer import Reply
from harness2.core.tree import SurfaceSpec
from harness2.substrate import registry
from harness2.substrate.manifest import (
    ExecSpec,
    GenericSubstrate,
    InstallSpec,
    SubstrateManifest,
    TrajectorySpec,
    discover_manifests,
    load_manifest,
    render_jsonl,
)

TREE = {
    "AGENTS.md": "# base\n\nWork carefully.\n",
    "skills/hello/SKILL.md": "---\nname: hello\ndescription: when greeting\n---\n# Hi\n",
    "scripts/sum.py": "print(1)\n",
}


def write_tree(root: Path, files=TREE) -> Path:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


class ManifestTests(unittest.TestCase):
    def test_shipped_manifests(self):
        got = {m.name: m for m in discover_manifests()}
        self.assertIn("dsh", got)
        self.assertIn("hermes", got)
        self.assertEqual(got["dsh"].install.mode, "home_dir")
        self.assertEqual(got["hermes"].install.mode, "split")
        for m in (got["dsh"], got["hermes"]):
            self.assertTrue(m.surface.skills_auto_advertised)
            self.assertFalse(m.surface.subagents_supported)
            self.assertEqual(m.trajectory.format, "text")
            self.assertGreater(m.surface.always_on_max, 0)

    def test_registry_aliases_and_unknown(self):
        self.assertIn("dsh", registry.names())
        self.assertEqual(registry.canonical("hm"), "hermes")
        self.assertIsInstance(registry.create("dsh"), GenericSubstrate)
        with self.assertRaises(ValueError) as ctx:
            registry.create("nope")
        self.assertIn("known:", str(ctx.exception))

    def test_auto_advertised_skill_needs_no_trigger(self):
        tree = HT.read_tree(
            load_manifest("harness2/substrate/manifests/dsh.json").surface,
            write_tree(Path(tempfile.mkdtemp())),
        )
        surface = load_manifest("harness2/substrate/manifests/dsh.json").surface
        census = HT.validate(surface, tree)
        self.assertEqual(census["skills"], 1)
        oc = load_manifest("harness2/substrate/manifests/dsh.json").surface
        strict = SurfaceSpec(
            name="strict",
            always_on=oc.always_on,
            always_on_max=oc.always_on_max,
            always_on_cap_reason="cap",
            skill_dir=oc.skill_dir,
            skill_name_rule="must_match",
            trigger_hint="name it",
            autotrigger_file=oc.always_on[0],
            trigger_line="- {name}\n",
            subagent_requires_mode=False,
            plugin_dir=None,
            hooks_file=None,
        )
        with self.assertRaises(ValueError):
            HT.validate(strict, tree)

    def test_split_install(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tree = write_tree(root / "tree")
            home = root / "home"
            home.mkdir()
            (home / "config.yaml").write_text("model: x\n", encoding="utf-8")
            (home / ".env").write_text("KEY=1\n", encoding="utf-8")
            import os

            old = os.environ.get("HERMES_HOME")
            os.environ["HERMES_HOME"] = str(home)
            try:
                sub = registry.create("hermes")
                install = sub.materialise(tree, sub.surface(), {})
            finally:
                if old is None:
                    os.environ.pop("HERMES_HOME", None)
                else:
                    os.environ["HERMES_HOME"] = old
            ws = Path(install.extras["workspace_dir"])
            hdir = Path(install.extras["home"])
            self.assertTrue((ws / "AGENTS.md").is_file())
            self.assertFalse((ws / "skills").exists())
            self.assertTrue((hdir / "skills/hello/SKILL.md").is_file())
            self.assertTrue((hdir / "config.yaml").is_file())
            self.assertTrue((hdir / ".env").is_file())

    def test_dsh_home_install(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tree = write_tree(root / "tree")
            seed = root / "seedhome"
            seed.mkdir()
            (seed / "settings.yaml").write_text("x: 1\n", encoding="utf-8")
            (seed / ".credentials.yaml").write_text("k: v\n", encoding="utf-8")
            import os

            old = os.environ.get("DSH_HOME")
            os.environ["DSH_HOME"] = str(seed)
            try:
                sub = registry.create("dsh")
                install = sub.materialise(tree, sub.surface(), {})
            finally:
                if old is None:
                    os.environ.pop("DSH_HOME", None)
                else:
                    os.environ["DSH_HOME"] = old
            home = Path(install.extras["home"])
            self.assertEqual(home, Path(install.staging_dir) / "home" / ".dsh")
            self.assertTrue((home / "AGENTS.md").is_file())
            self.assertTrue((home / "skills/hello/SKILL.md").is_file())
            self.assertTrue((home / "settings.yaml").is_file())
            self.assertTrue((home / ".credentials.yaml").is_file())

    def test_jsonl_renderer(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s.jsonl"
            rows = [
                {
                    "type": "user/message",
                    "role": "user",
                    "content": [{"type": "text", "text": "do it"}],
                },
                {
                    "type": "assistant/message",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "on it"}],
                    },
                },
                {"type": "tool/call", "data": {"name": "bash", "arguments": '{"cmd":"ls"}'}},
                {
                    "type": "tool/result",
                    "data": {
                        "message": {
                            "content": [
                                {
                                    "type": "tool-result",
                                    "content": [{"type": "text", "text": "a.txt"}],
                                }
                            ]
                        }
                    },
                },
            ]
            p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            got = render_jsonl(p, obs_trunc=1000, max_chars=10000)
            self.assertIn("[USER] do it", got)
            self.assertIn("[ASSISTANT] on it", got)
            self.assertIn("[ACTION]", got)
            self.assertIn("[OBSERVATION]", got)
            self.assertIn("a.txt", got)


class LocalBenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _fake_substrate(self):
        script = self.root / "fake_agent.py"
        script.write_text(
            "import pathlib, sys\n"
            "prompt = sys.argv[1]\n"
            "(pathlib.Path('output')).mkdir(exist_ok=True)\n"
            "(pathlib.Path('output') / 'answer.txt').write_text('answered: ' + prompt.strip().splitlines()[0])\n"
            "print('[ACTION] wrote output/answer.txt')\n"
            "print('[OBSERVATION] done')\n",
            encoding="utf-8",
        )
        surface = SurfaceSpec(
            name="fake",
            always_on=("AGENTS.md",),
            always_on_max=4000,
            always_on_cap_reason="test",
            skill_dir="skills",
            skill_name_rule="must_match",
            trigger_hint="name it",
            autotrigger_file="AGENTS.md",
            trigger_line="- {name}\n",
            subagent_requires_mode=False,
            plugin_dir=None,
            hooks_file=None,
            skills_auto_advertised=True,
            subagents_supported=False,
        )
        manifest = SubstrateManifest(
            name="fake",
            label="Fake substrate",
            surface=surface,
            install=InstallSpec(mode="workspace_root", entries=("AGENTS.md", "skills", "scripts")),
            exec=ExecSpec(argv=(sys.executable, str(script), "{prompt}"), timeout=30),
            trajectory=TrajectorySpec(format="text", path="session.log"),
        )
        registry.register(
            "fake", lambda launcher=None: GenericSubstrate(manifest, launcher=launcher)
        )

    def _local_root(self):
        root = self.root / "bench"
        write_tree(root / "base")
        task = root / "tasks" / "accounting" / "t1"
        task.mkdir(parents=True)
        (task / "task.md").write_text("Add the numbers.\n", encoding="utf-8")
        return root

    def test_execute_and_deliverable(self):
        self._fake_substrate()
        adapter = LocalAdapter(self._local_root(), substrate="fake")
        run_dir = adapter.execute("accounting/t1", None, "local/test/run1")
        self.assertTrue(adapter.delivered(run_dir))
        self.assertIn("answered: Add the numbers.", adapter.deliverable_text(run_dir))
        self.assertIn("[ACTION]", adapter.trajectory(run_dir, obs_trunc=1000))
        # idempotent: a second execute reuses the completed run
        before = (run_dir / "meta.json").read_text()
        adapter.execute("accounting/t1", None, "local/test/run1")
        self.assertEqual(before, (run_dir / "meta.json").read_text())

    def test_end_to_end_run_task(self):
        self._fake_substrate()
        adapter = LocalAdapter(self._local_root(), substrate="fake")

        class FakeProposer:
            def __init__(self, spec):
                self.spec = spec
                self.labels = []

            def call(self, mission, context_dir, label, **kwargs):
                ws = Path(context_dir) / "harness"
                (ws / "AGENTS.md").write_text(f"# edited by {label}\n", encoding="utf-8")
                self.labels.append(label)
                return Reply(label, "<notes>edited</notes>", True, attempts=1, workspace=ws)

        prop = FakeProposer(adapter.spec.channels["proposer"])
        result = schedule.run_task(
            "accounting/t1",
            1,
            adapter=adapter,
            prop=prop,
            runs_root=self.root / "runs",
            modes=("agg",),
        )
        self.assertEqual(result.status, "ok")
        summary = json.loads(next((self.root / "runs").rglob("summary.json")).read_text())
        ledger = summary["modes"]["agg"]["ledger"]
        self.assertEqual((ledger["R"], ledger["P"]), (4, 3))
        self.assertEqual(summary["selections"]["agg"]["chosen_label"], "agg1")
        chosen = Path(summary["selections"]["agg"]["chosen_run_dir"])
        self.assertIn("answered:", adapter.deliverable_text(chosen))
        self.assertEqual(sorted(prop.labels), ["agg1.a1", "dom1.a1", "inv1.a1"])

    def test_split_host_and_check_script(self):
        """Workspace files + $HOME files staged separately, then scored by check.sh."""
        script = self.root / "split_agent.py"
        script.write_text(
            "import os, pathlib, sys\n"
            "home = pathlib.Path(os.environ['HERMES_HOME'])\n"
            "assert (home / 'skills' / 'hello' / 'SKILL.md').is_file(), 'skill not staged in home'\n"
            "assert pathlib.Path('AGENTS.md').is_file(), 'workspace instructions missing'\n"
            "(pathlib.Path('output')).mkdir(exist_ok=True)\n"
            "(pathlib.Path('output') / 'answer.txt').write_text('ok')\n",
            encoding="utf-8",
        )
        surface = SurfaceSpec(
            name="splitfake",
            always_on=("AGENTS.md",),
            always_on_max=4000,
            always_on_cap_reason="test",
            skill_dir="skills",
            skill_name_rule="must_match",
            trigger_hint="name it",
            autotrigger_file="AGENTS.md",
            trigger_line="- {name}\n",
            subagent_requires_mode=False,
            plugin_dir=None,
            hooks_file=None,
            skills_auto_advertised=True,
            subagents_supported=False,
        )
        manifest = SubstrateManifest(
            name="splitfake",
            label="Split fake",
            surface=surface,
            install=InstallSpec(
                mode="split",
                root=".home",
                entries=("AGENTS.md",),
                home_entries=("skills",),
                seed_env="SPLIT_SEED",
                seed_files=("config.yaml",),
            ),
            exec=ExecSpec(
                argv=(sys.executable, str(script)),
                env={"HERMES_HOME": "{home}"},
                timeout=30,
            ),
            trajectory=TrajectorySpec(format="text", path="session.log"),
        )
        registry.register(
            "splitfake", lambda launcher=None: GenericSubstrate(manifest, launcher=launcher)
        )
        root = self.root / "splitbench"
        write_tree(root / "base")
        task = root / "tasks" / "d" / "t"
        task.mkdir(parents=True)
        (task / "task.md").write_text("do it\n", encoding="utf-8")
        (task / "check.sh").write_text(
            '#!/bin/sh\ntest -f "$OUTPUT_DIR/answer.txt" && grep -q ok "$OUTPUT_DIR/answer.txt"\n',
            encoding="utf-8",
        )
        seed = self.root / "seedhome"
        seed.mkdir()
        (seed / "config.yaml").write_text("x: 1\n", encoding="utf-8")
        import os

        old = os.environ.get("SPLIT_SEED")
        os.environ["SPLIT_SEED"] = str(seed)
        try:
            adapter = LocalAdapter(root, substrate="splitfake")
            run_dir = adapter.execute("d/t", None, "local/split/run1")
        finally:
            if old is None:
                os.environ.pop("SPLIT_SEED", None)
            else:
                os.environ["SPLIT_SEED"] = old
        self.assertTrue(adapter.delivered(run_dir))
        score = adapter.score(run_dir, "d/t")
        self.assertIsNotNone(score)
        self.assertTrue(score["all_pass"])


if __name__ == "__main__":
    unittest.main()
