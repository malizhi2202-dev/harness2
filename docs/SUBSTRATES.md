# Substrates: OpenCode, Codex, DSH, Hermes, and your own

A **substrate** is the agent framework a solver rollout runs under. Harness² only edits that
framework's *editable surface* (its prompts, skills, scripts, plugins/hooks), never its code.

| substrate | token | how it is implemented | runs on |
|---|---|---|---|
| OpenCode | `oc` | Python class (`substrate/opencode.py`), three install modes | `jb`, `lab`, `wb` |
| Codex | `cx` | Python class (`substrate/codex.py`), `codex_root` | `jb`, `lab`, `wb` |
| **DSH** | `dsh` | JSON manifest (`substrate/manifests/dsh.json`) | `local` (any task dir) |
| **Hermes** | `hermes` (`hm`) | JSON manifest (`substrate/manifests/hermes.json`) | `local` (any task dir) |
| your framework | any name | a JSON manifest you drop in a manifest dir | `local` (any task dir) |

`oc` / `cx` keep native benchmark launchers because JobBench/LAB/WorkBuddy own artifact
extraction and grading. Other substrates run through the **local benchmark**: a directory of
tasks with a prompt, optional input files, and an optional pass/fail check. Native launchers
for the three professional benchmarks are a per-benchmark change, not a substrate change.

---

## 1. Quick start

```bash
# a task directory (see §4)
export HARNESS2_LOCAL_TASKS=$PWD/my-bench
export HARNESS2_IMPROVER_MODEL=provider/model   # the proposer/selector model (required)
export HARNESS2_DSH_BIN=/path/to/dsh          # or a wrapper on PATH
export HARNESS2_DSH_MODEL=provider/model      # recorded in the run identity only
harness2-check --bench local --substrate dsh
harness2 --bench local --substrate dsh --k 1 --mode parallel --dry-run
harness2 --bench local --substrate dsh --k 1 --mode parallel
harness2-judge --bench local --substrate dsh --k 1 --mode parallel
harness2-report --bench local --substrate dsh --k 1 --mode parallel
```

`--substrate` accepts the names in the first column (plus aliases `opencode`, `codex`, `hm`).
`--local-tasks` overrides `HARNESS2_LOCAL_TASKS`.

### Using the same framework as the improver

The proposer/selector channel is independent of the solver substrate. With DSH and Hermes
installed, you can run the whole loop without OpenCode or the Claude CLI:

```bash
# DSH solves AND improves
export HARNESS2_IMPROVER_CHANNEL=dsh_cli
export HARNESS2_DSH_BIN=/path/to/dsh
export HARNESS2_DSH_MODEL=deepseek-flash   # recorded in the identity; DSH uses its own settings.yaml

# Hermes solves AND improves
export HARNESS2_IMPROVER_CHANNEL=hermes_cli
export HARNESS2_HERMES_BIN=/path/to/hermes
export HARNESS2_HERMES_MODEL=deepseek-v4-pro
```

The channels are `opencode` (default), `claude_cli`, `dsh_cli`, `hermes_cli`. `dsh_cli` runs
`dsh --profile headless <mission>` in the copied evidence workspace with
`DSH_PERMISSION_MODE=danger-full-access`; `hermes_cli` runs `hermes -z <mission>` (which sets
`HERMES_YOLO_MODE=1`). Both return the final assistant text as the `<notes>` reply and harvest
`context/harness/`. They use the real `$DSH_HOME` / `$HERMES_HOME`, so sessions land in the
normal local stores.

---

## 2. DSH (DeepSeek Harness)

DSH's editable surface is split between `$DSH_HOME` (default `~/.dsh`) and the project root.

| component | tree path | installed to | why |
|---|---|---|---|
| c1 instructions | `AGENTS.md` | `$DSH_HOME/AGENTS.md` | DSH loads the user-global file into every request (project `AGENTS.md` also works, but the global one survives any cwd) |
| c6 skills | `skills/<name>/SKILL.md` | `$DSH_HOME/skills/` | rank-400 user skills; DSH advertises name+description and loads the body on demand |
| c7 scripts | `scripts/*.py` | `<workspace>/scripts/` | deterministic helpers; they must be on the solver's cwd path, not in `$DSH_HOME` |

**Install** is a `split` layout: instructions + skills go to a temporary `$DSH_HOME`
(seeded from the real home so profile/model/credentials still work), while `scripts/` is
staged into the workspace where the solver runs:

```json
"install": {
  "mode": "split", "root": ".dsh",
  "entries": ["scripts"],
  "home_entries": ["AGENTS.md", "skills"],
  "seed_env": "DSH_HOME", "seed_default": "~/.dsh",
  "seed_files": ["settings.yaml", ".credentials.yaml", ".env"]
}
```

**Exec**: `dsh --profile headless "<task>"` with `DSH_HOME` pointing at the staged home and
`DSH_PERMISSION_MODE=danger-full-access` (headless has no approval UI; the manifest sets it).
`dsh` must be resolvable: put it on `PATH` or set `HARNESS2_DSH_BIN` (the built
`apps/cli/lib/bin.js` is executable, so it can be the value directly).

**Surface rules that differ from OpenCode/Codex**:

- skills are **auto-advertised**, so a trigger line in `AGENTS.md` is not required
  (`skills_auto_advertised: true` in the manifest);
- there is **no file-based subagent surface** (`subagents_supported: false`): DSH subagents are
  provider/tool configuration, not a directory of markdown files;
- hooks/plugins are Cordis patch lines, not a workspace file, so this surface exposes neither.

**Known limitation**: `headless` prints only the final answer, and DSH's full session log is
zstd-compressed JSONL under `$DSH_HOME/sessions/`. This release's trajectory is therefore the
text stdout/stderr; the JSONL event mapping (see the DSH session format) is the next upgrade.

---

## 3. Hermes Agent

Hermes reads project instructions **only from the working directory** (first found wins:
`.hermes.md`/`HERMES.md` → `AGENTS.md` → `CLAUDE.md` → `.cursorrules`), while skills, config and
credentials live under `$HERMES_HOME` (default `~/.hermes`). That is why Hermes uses a **split**
install: workspace files and home files staged separately.

| component | tree path | installed to | why |
|---|---|---|---|
| c1 instructions | `AGENTS.md` | `<workspace>/AGENTS.md` | context-tier project instructions; only cwd is scanned |
| c6 skills | `skills/<name>/SKILL.md` | `$HERMES_HOME/skills/` | Hermes' local skill root; the index is advertised, bodies load on demand |
| c7 scripts | `scripts/*.py` | `<workspace>/scripts/` | deterministic helpers |

```json
"install": {
  "mode": "split", "root": ".hermes",
  "entries": ["AGENTS.md", "scripts"],
  "home_entries": ["skills"],
  "seed_env": "HERMES_HOME", "seed_default": "~/.hermes",
  "seed_files": ["config.yaml", ".env"]
}
```

**Exec**: `hermes -z "<task>"`, the documented one-shot mode: no banner, final answer on stdout,
cwd is the workspace, and it sets `HERMES_YOLO_MODE=1`. Set `HARNESS2_HERMES_BIN` to the
installed `hermes` executable when it is not on `PATH`.

**Surface rules**:

- skills are **auto-advertised** by the system-prompt index; no trigger line is required;
- there is **no declarative subagent directory** (delegation is a tool call): `subagents_supported: false`;
- plugins are Python (`plugins/<id>/plugin.yaml` + `register(ctx)`) and hooks are either
  `config.yaml` shell hooks (needing an allowlist) or gateway-only `hooks/`; this release does
  not expose them as editable components;
- the skill index is cached in `$HERMES_HOME/.skills_prompt_snapshot.json`; the staged home is
  fresh per rollout, so no stale snapshot is possible.

**Known limitation**: `-z` writes only the final reply to stdout, so the trajectory is textual.
For tool-level evidence, `hermes-agent --query ... --save_trajectories True` emits ShareGPT
JSONL (`trajectory_samples.jsonl`); wiring that renderer is a small follow-up (the manifest
already supports `trajectory.format: jsonl`).

---

## 4. The local benchmark

```
<root>/
  base/                          the baseline harness tree for this substrate
  tasks/<domain>/<task>/
    task.md                      the prompt (required)
    files/                       optional inputs, copied to workspace/input/
    check.sh                     optional pass/fail check (exit 0 = pass)
```

Run directories are content-addressed and reusable. They live **outside the task tree**
(`/tmp/harness2-local/<root>-<hash>/executions/…` by default, or `HARNESS2_LOCAL_EXEC_DIR`),
so a solver that walks up from its workspace cannot reach `tasks/.../check.sh`:

```
<exec-root>/executions/<namespace>__<task>__<label>/
  workspace/                     the tree as the framework read it
  workspace/input/               task files
  output/                        everything the run produced
  session.log                    captured stdout/stderr
  prompt.md  meta.json  .adapt_complete
```

Summaries, adopted harnesses and the library stay under `<root>/runs/`.

`check.sh` runs after adoption with `OUTPUT_DIR`, `WORKSPACE` and `TASK_DIR` set; without it a
task is **unscored**, never zero. This keeps the local benchmark honest without a rubric.

---

## 5. Your own framework: a JSON manifest

Manifests are discovered in:

1. `harness2/substrate/manifests/` (shipped);
2. each directory in `HARNESS2_SUBSTRATE_DIRS` (colon-separated);
3. `~/.harness2/substrates/`.

Later files override earlier ones with the same `name`. The full schema:

```json
{
  "name": "myfw",
  "label": "My Framework",
  "surface": {
    "always_on": ["AGENTS.md"],
    "always_on_max": 16000,
    "always_on_cap_reason": "injected into every request",
    "skill_dir": "skills",
    "skill_name_rule": "must_match",
    "skills_auto_advertised": true,
    "subagents_supported": false,
    "subagent_dir": "sub_agents",
    "plugin_dir": null,
    "hooks_file": null,
    "hooks_schema": "none",
    "compile_skill_scripts": true,
    "reject_empty_always_on": true,
    "trigger_line": "- When {desc}, load the `{name}` skill.\n",
    "trigger_hint": "name the skill in the always-on text",
    "stubs": [{"path": "skills/README.md", "body": "<!-- ... -->\n"}]
  },
  "install": {
    "mode": "workspace_root",
    "entries": ["AGENTS.md", "skills", "scripts"]
  },
  "exec": {
    "argv": ["{bin}", "--run", "{prompt}"],
    "stdin": null,
    "env": {"MYFW_HOME": "{home}"},
    "bin_env": "HARNESS2_MYFW_BIN",
    "default_bin": "myfw",
    "timeout": 3600
  },
  "trajectory": {"format": "text", "path": "session.log"}
}
```

Placeholders: `{bin}`, `{workspace}`, `{prompt}`, `{model}`, `{home}`. `install.mode` is
`workspace_root`, `home_dir` (needs `root`) or `split` (needs `root` + `home_entries`); seed
`config`/credentials with `seed_env` + `seed_files`. `trajectory.format` is `text` or `jsonl`
(the JSONL reader tolerates common field names and maps tool calls/results/messages).

A manifest substrate is executable only through `--bench local` in this release. If you also
want it on `jb`/`lab`/`wb`, implement that benchmark's launcher (workspace + prompt + artifact
extraction + judge); the substrate itself does not change.

---

## 6. Security notes

- **DSH** is run with `DSH_PERMISSION_MODE=danger-full-access` because headless has no approval
  channel. Its sandbox only constrains file effects, not network/process visibility.
- **Hermes** `-z` sets `HERMES_YOLO_MODE=1` (hardline command blacklist still applies).
- A manifest can point `{bin}` at anything: treat user manifest directories as code, like a
  plugin directory.
- None of this is an OS sandbox. Run substrates inside a container or a disposable VM when the
  task data is untrusted.

---

## 7. Verification

```bash
pytest tests/test_substrates.py -q        # manifests, split/home installs, local e2e, JSONL
pytest tests -q                          # full suite
```

The end-to-end test registers a fake manifest substrate and a fake CLI, runs a real
`schedule.run_task` (k=1), and asserts the 4k/3k ledger, the adopted final, and the produced
artifact — no model credentials involved.
