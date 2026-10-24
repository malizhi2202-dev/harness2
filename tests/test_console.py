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

"""The console reader: real run artefacts -> the model the product page renders."""

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from harness2 import console_activity, console_data
from harness2.cli import console


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _fixture(root: Path) -> Path:
    """One run with a library and two executions, laid out like a real runs tree."""
    task = root / "runs" / "demo-oc-agg2" / "ledger-1"
    _write(task / "task.md", "# Ledger total\n\nSum the signed amounts.\n")
    summary = {
        "task_id": "ledger-1",
        "domain": "accounting",
        "k": 2,
        "seed": None,
        "status": "ok",
        "base": [{"label": "base1", "run_id": "demo-oc-agg2/ledger-1/base1",
                  "run_dir": str(root / "executions" / "demo-oc-agg2" / "ledger-1" / "base1")}],
        "modes": {"agg": {
            "mode": "agg", "task_id": "ledger-1", "k": 2, "finals": ["agg1"], "ok": True,
            "fell_back": [], "kept_base": [],
            "stages": [
                {"label": "inv1", "dim": "INV", "attempts": 1, "cached": False,
                 "fell_back": False, "kept_base": False, "usd": 0.0, "error": "",
                 "notes": "Keep signed amounts."},
            ],
            "similarity": {"n": 2, "n_distinct": 1, "mean_line_jaccard": 1.0},
            "ledger": {"mode": "agg", "task_id": "ledger-1", "k": 2, "R": 8, "expected_R": 8,
                       "P": 6, "expected_P": 6, "session_attempts": 6, "usd": 0.0,
                       "rollouts": ["base1", "base2", "inv1", "dom1", "inv2", "dom2", "agg1", "agg2"],
                       "sessions": ["inv1", "dom1", "inv2", "dom2", "agg1", "agg2"]},
            "errors": [],
        }},
        "selections": {"agg": {
            "task_id": "ledger-1", "mode": "agg", "k": 2, "chosen_id": "A",
            "chosen_label": "agg1", "chosen_run_id": "demo-oc-agg2/ledger-1/agg1",
            "chosen_is_base": False, "why": "The signed total matches the source ledger.",
            "dropped": [], "n_candidates": 1, "ok": True, "trivial": False,
            "expected": "deliverable",
            "chosen_run_dir": str(root / "executions" / "demo-oc-agg2" / "ledger-1" / "agg1"),
            "experience": {"cue": "When a ledger includes credits and debits",
                           "lesson": "Include both signs.", "evidence": "inv1 moved 105.25 to 85.15",
                           "confidence": "high"},
        }},
        "errors": [],
    }
    _write(task / "summary.json", json.dumps(summary))
    for label, body in (("base1", "Total: 105.25\n"), ("agg1", "Total: 85.15\n")):
        ex = root / "executions" / "demo-oc-agg2" / "ledger-1" / label
        _write(ex / "answer.txt", body)
        _write(ex / "trajectory.md", f"# {label}\n\n[ACTION]\nrun helper\n")
    lib = root / "library" / "k2" / "agg" / "accounting"
    _write(lib / "INDEX.md", "# Library — accounting\n\n## `harnesses/ledger-1/`\n")
    _write(lib / "harnesses" / "ledger-1" / "EXPERIENCE.md", (
        "# ledger-1\n\n"
        "- adopted: `agg1`  (run `demo-oc-agg2/ledger-1/agg1`)\n"
        "- components: 1 scripts; 62 always-on chars\n\n"
        "## Why this was chosen\nThe signed total matches the source ledger.\n\n"
        "## What each edit did\ncomponent: scripts/summarize.py\ncandidates: inv1\n"
        "change: dropped the positive-only filter\nobserved: the probe included credits\n"
        "verdict: helped\n\n"
        "## Transferable lesson\n"
        "- **cue**: When a ledger includes credits and debits\n"
        "- **lesson**: Include both signs when summing.\n"
        "- **evidence**: inv1 moved 105.25 to 85.15\n"
        "- **confidence**: high\n"
    ))
    return root


class ConsoleDataTests(unittest.TestCase):
    def test_collect_reads_real_artefacts(self):
        root = _fixture(Path(tempfile.mkdtemp()))
        model = console_data.collect([root])

        self.assertEqual(model["meta"]["focus"], "demo-oc-agg2")
        self.assertEqual(model["run"]["namespace"], "demo-oc-agg2")
        self.assertEqual(model["run"]["k"], 2)
        self.assertEqual(len(model["tasks"]), 1)
        task = model["tasks"][0]
        self.assertEqual(task["base"], "Total: 105.25")
        self.assertEqual(task["final"], "Total: 85.15")
        self.assertEqual(task["adopted"], "agg1")
        self.assertEqual(task["score"], "unscored")  # no judged.json -> never invented

        detail = model["run_detail"]
        self.assertEqual(detail["id"], "ledger-1")
        self.assertEqual([b["label"] for b in detail["baselines"]], ["base1"])
        self.assertEqual(detail["baselines"][0]["artifact"], "Total: 105.25")
        self.assertTrue(detail["baselines"][0]["delivered"])
        self.assertEqual(detail["selection"]["adopted"], "agg1")
        self.assertIn("signed total", detail["selection"]["rationale"][0])
        self.assertEqual([p["id"] for p in detail["phases"]], ["inv1", "agg1"])

        kpis = {k["k"]: k["v"] for k in detail["kpis"]}
        self.assertEqual(kpis["rollouts"], "8 / 8")
        self.assertEqual(kpis["编辑会话"], "6 / 6")

        # library: parsed from disk, cue/lesson survive
        self.assertEqual([d["id"] for d in model["library"]["domains"]], ["accounting"])
        entry = model["library"]["entries"]["accounting"][0]
        self.assertEqual(entry["id"], "ledger-1")
        self.assertEqual(entry["adopted"], "agg1")
        self.assertIn("credits and debits", entry["cue"])
        self.assertEqual(entry["edits"][0]["verdict"], "helped")
        self.assertIn("signed total matches", entry["why"])

        # evidence: real files, with bodies
        self.assertIn("summary.json", model["evidence"]["files"])
        self.assertIn("executions/base1/answer.txt", model["evidence"]["files"])
        self.assertEqual(model["evidence"]["files"]["executions/agg1/answer.txt"]["body"].strip(),
                         "Total: 85.15")
        self.assertIn("traj", [t["id"] for t in model["evidence"]["tabs"]])

        # no judge output -> the report says so instead of inventing deltas
        self.assertTrue(model["report"]["empty"])
        self.assertTrue(any("judged.json" in e["text"] for e in model["events"]))

    def test_empty_root_is_a_clear_error(self):
        with self.assertRaises(SystemExit) as ctx:
            console_data.collect([Path(tempfile.mkdtemp())])
        self.assertIn("no harness2 runs found", str(ctx.exception))

    def test_export_bakes_the_model(self):
        root = _fixture(Path(tempfile.mkdtemp()))
        out = Path(tempfile.mkdtemp()) / "console.html"
        rc = console.main(["--runs", str(root), "--export", str(out)])
        self.assertEqual(rc, 0)
        html = out.read_text(encoding="utf-8")
        self.assertIn("window.__H2_DATA__", html)
        self.assertIn("demo-oc-agg2", html)
        self.assertIn("Total: 85.15", html)
        # the standalone page still carries the demo fallback for file:// use
        self.assertIn("let RUN = {", html)


def _serve(root: Path):
    """Serve the console over a fixture on an ephemeral port."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), console._handler([root]))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


class ConsoleServerTests(unittest.TestCase):
    def setUp(self):
        self.root = _fixture(Path(tempfile.mkdtemp()))
        self.httpd, self.base = _serve(self.root)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def _post(self, path, payload):
        req = urllib.request.Request(self.base + path, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, dict(res.headers), res.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def _get(self, path, headers=None):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, dict(res.headers), res.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def test_state_is_json_with_etag_and_revision(self):
        status, headers, body = self._get("/api/state")
        self.assertEqual(status, 200)
        self.assertIn("json", headers["Content-Type"])
        model = json.loads(body)
        self.assertEqual(model["run"]["namespace"], "demo-oc-agg2")
        self.assertEqual(model["meta"]["revision"], console_data.signature([self.root]))
        self.assertTrue(headers.get("ETag", "").strip('"') == model["meta"]["revision"])
        self.assertIn("read_at", model["meta"])

    def test_unchanged_tree_answers_304(self):
        _status, headers, _body = self._get("/api/state")
        etag = headers["ETag"]
        status, _h, body = self._get("/api/state", {"If-None-Match": etag})
        self.assertEqual(status, 304)
        self.assertEqual(body, b"")

    def test_change_moves_the_revision(self):
        first = console_data.signature([self.root])
        summary = self.root / "runs" / "demo-oc-agg2" / "ledger-1" / "summary.json"
        summary.write_text(summary.read_text(encoding="utf-8"), encoding="utf-8")
        second = console_data.signature([self.root])
        self.assertNotEqual(first, second)

    def test_events_stream_starts_with_ready(self):
        req = urllib.request.Request(self.base + "/api/events")
        with urllib.request.urlopen(req, timeout=5) as res:
            self.assertIn("text/event-stream", res.headers["Content-Type"])
            chunk = b""
            while b"\n\n" not in chunk and len(chunk) < 400:
                chunk += res.read(1)  # the stream stays open: read the first event only
            text = chunk.decode("utf-8", "replace")
            self.assertIn("event: ready", text)
            self.assertIn("revision", text)

    def test_read_only(self):
        req = urllib.request.Request(self.base + "/api/state", data=b"{}", method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            self.fail("POST must be rejected")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 405)

    def test_config_endpoint_reports_sources_and_tools(self):
        status, _headers, body = self._get("/api/config")
        self.assertEqual(status, 200)
        config = json.loads(body)
        self.assertTrue(config["server"]["read_only"])
        self.assertFalse(config["server"]["allow_run"])
        self.assertEqual(config["server"]["port"], self.httpd.server_address[1])
        self.assertEqual(config["roots"], [{"path": str(self.root), "exists": True}])
        self.assertIn("harness2", config["executables"])
        self.assertTrue(any("/api/activity" in row[0] for row in config["endpoints"]))

    def test_task_query_selects_the_detail(self):
        extra = self.root / "runs" / "demo-oc-agg2" / "ledger-2"
        extra.mkdir(parents=True)
        clone = json.loads(
            (self.root / "runs" / "demo-oc-agg2" / "ledger-1" / "summary.json").read_text())
        clone["task_id"] = "ledger-2"
        (extra / "summary.json").write_text(json.dumps(clone), encoding="utf-8")
        _status, _headers, body = self._get("/api/state?task=ledger-2")
        model = json.loads(body)
        self.assertEqual(model["run_detail"]["id"], "ledger-2")
        self.assertEqual({t["id"] for t in model["tasks"]}, {"ledger-1", "ledger-2"})
        self.assertEqual([t["id"] for t in model["tasks"] if t["detail"]], ["ledger-2"])

    def test_rerun_plan_and_guarded_post(self):
        plan = console.rerun_plan([self.root], "demo-oc-agg2", "ledger-1")
        self.assertIn("harness2", plan["command"])
        self.assertIn("--k 2", plan["command"])
        # the fixture is a demo run: the plan replays the deterministic demo chain
        self.assertIn("harness2-demo", plan["command"])
        self.assertEqual(plan["run"], "demo-oc-agg2")
        status, _h, body = self._get("/api/rerun?run=demo-oc-agg2&task=ledger-1")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["allow_run"])
        status, _h, _b = self._post("/api/rerun", {"run": "demo-oc-agg2", "task": "ledger-1"})
        self.assertEqual(status, 403)  # execution needs --allow-run

    def test_rerun_plan_for_a_local_benchmark(self):
        root = _fixture(Path(tempfile.mkdtemp()))
        local = root / "runs" / "adapt" / "local-dsh-agg1" / "accounting__ledger"
        local.mkdir(parents=True)
        clone = json.loads((root / "runs" / "demo-oc-agg2" / "ledger-1" / "summary.json").read_text())
        clone["task_id"] = "accounting/ledger"
        (local / "summary.json").write_text(json.dumps(clone), encoding="utf-8")
        plan = console.rerun_plan([root], "local-dsh-agg1", "accounting/ledger")
        self.assertIn("harness2 --bench local", plan["command"])
        self.assertIn("--substrate dsh", plan["command"])
        self.assertIn(f"--local-tasks {root}", plan["command"])
        self.assertIn("--only-domains accounting", plan["command"])

    def test_activity_endpoint_reports_recent_writes(self):
        _summary = self.root / "runs" / "demo-oc-agg2" / "ledger-1" / "summary.json"
        _status, _headers, body = self._get("/api/activity?minutes=60&limit=20")
        self.assertEqual(_status, 200)
        activity = json.loads(body)
        self.assertIn("processes", activity)
        self.assertIn("active", activity)
        self.assertIn("events", activity)
        self.assertGreaterEqual(activity["events_total"], 1)
        kinds = {e["kind"] for e in activity["events"]}
        self.assertTrue({"summary", "artifact"} & kinds, kinds)
        self.assertTrue(any(a["run"] == "demo-oc-agg2" for a in activity["active"]))

    def test_serves_the_console_page(self):
        status, headers, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("loadLive", body.decode("utf-8", "replace"))


class ConsoleActivityTests(unittest.TestCase):
    def test_phase_follows_the_artefacts(self):
        root = _fixture(Path(tempfile.mkdtemp()))
        task = root / "runs" / "demo-oc-agg2" / "ledger-1"
        activity = console_activity.collect([root], minutes=60, limit=50)
        active = {a["task"]: a for a in activity["active"]}
        self.assertEqual(active["ledger-1"]["phase"], "已出 summary")
        # a lock newer than the summary means the summary is being rewritten
        lock = task / "summary.lock"
        lock.write_text("", encoding="utf-8")
        later = task / "summary.json"
        import os as _os
        _os.utime(later, (later.stat().st_atime, later.stat().st_mtime - 5))
        activity = console_activity.collect([root], minutes=60, limit=50)
        active = {a["task"]: a for a in activity["active"]}
        self.assertEqual(active["ledger-1"]["phase"], "正在写 summary")
        self.assertIn("lock", {e["kind"] for e in activity["events"]})

    def test_no_recent_writes_means_no_events(self):
        root = _fixture(Path(tempfile.mkdtemp()))
        stale = console_activity.collect([root], minutes=1, limit=10)
        import os as _os
        old = 0  # epoch: everything predates the window
        for path in root.rglob("*"):
            if path.is_file():
                _os.utime(path, (old, old))
        activity = console_activity.collect([root], minutes=1, limit=10)
        self.assertEqual(activity["events_total"], 0)
        self.assertEqual(activity["active"], [])
        del stale


if __name__ == "__main__":
    unittest.main()
