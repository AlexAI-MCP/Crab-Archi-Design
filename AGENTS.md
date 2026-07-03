# AGENTS.md — Operating Contract for Coding Agents (Codex, Claude Code, others)

Crab Archi Design is an original-SVG-first framework for architectural community
layout design. This file is the agent-facing contract: follow it exactly whether
you are Codex CLI, Claude Code, or any other agent runtime.

## Hard rules (never break these)

1. **LLMs never write SVG coordinates.** You author `DesignIntent` / `EditIntent`
   JSON and natural-language prompts. The deterministic solver and the
   `same-layer-svg-engine` compute all geometry.
2. **OpenCrab MCP evidence is required** before any design alternative.
   Attach it with `opencrab-sync` (result JSON) or `evidence-attach`.
3. **Protected geometry is inviolable**: parking, columns, cores, ramps, stairs,
   egress, and the community outer shell. The engine enforces
   `locked_geometry_unchanged`; do not try to work around a failed gate.
4. **Native SVG only.** No raster overlays, no zoning-overlay redraw layers.
   Final candidates mutate existing source SVG elements in place.
5. **Do not commit proprietary drawings or project outputs.** `projects/`,
   `*.svg` (except tracked examples), and exports are gitignored by policy.

## Setup

```bash
python -m pip install -e ".[test]"   # Python 3.9+
python -m pytest -q                  # must pass before and after your changes
```

## The production path (one command per phase)

New project → candidate:

```bash
crab-archi-design --project-root projects workflow-run \
  --project-id <id> \
  --source-svg /abs/path/drawing.svg \
  --households 900 \
  --standards /abs/path/standards.csv \
  --ontology-pack community_svg_topology_ontology_v2 \
  --opencrab-result-file /abs/path/opencrab_result.json \
  --constraint-sketch /abs/path/constraint_sketch.json \
  --scale-mm-per-world 84 --scale-evidence "User-confirmed grid dimension." \
  --prompt "<design request>" \
  --engine-adapter same-layer-svg-engine \
  --engine-arg=--apply-program-relabels \
  --engine-arg=--apply-openings \
  --engine-arg=--apply-endpoint-moves \
  --skip-preview --strict
```

Repeat edits on an existing project:

```bash
crab-archi-design --project-root projects revision-run \
  --project-id <id> \
  --text "<revision request>" \
  --sketch /abs/path/edit_sketch.json \
  --engine-arg=--apply-program-relabels \
  --engine-arg=--apply-openings \
  --engine-arg=--apply-endpoint-moves \
  --skip-preview --strict
```

With `same-layer-svg-engine`, both commands automatically run
`recognition-audit` and `svg-patch-plan` after topology. `--strict` exits
non-zero unless the final status is `pass` — treat a non-zero exit as a gate
failure to diagnose, not to bypass. Ship-readiness is:

```bash
crab-archi-design --project-root projects export-package --project-id <id>
crab-archi-design --project-root projects release-audit --project-id <id> \
  --zip projects/<id>/exports/<id>_export_001.zip --strict
```

## Localhost studio (human-in-the-loop annotation)

```bash
crab-archi-design studio --project-root projects --port 8765 --open
```

The studio serves `http://127.0.0.1:8765/` where an operator loads the source
linework and marks, directly on the drawing: protected zones (community shell,
no-go, lock), space adjustments (mutable/projectable zones, program expansion),
wall adjustments (openings, partition removals, wall moves), and the
natural-language request. `POST /api/run` converts those annotations into
constraint/edit sketch JSON and executes the same tested pipeline above. Run
reports land in `projects/<id>/studio/studio_run_###/`.

Agents may drive the same API headlessly:

- `GET  /api/health` — modes and project root
- `POST /api/load-svg {"path": "/abs/file.svg"}` — register + fetch linework
- `POST /api/run {project_id, source_svg, prompt, strokes[], scale, ...}` —
  stroke modes: `community_shell`, `no_go_zone`, `lock_boundary`,
  `mutable_zone`, `projectable_zone`, `open_connection`, `remove_partition`,
  `move_wall`, `expand_program_feel`
- `GET  /api/artifact?path=...` — fetch produced SVG/report/panel
- `GET  /api/status?project_id=...` — readiness gates

## MCP server (for Codex `mcp add` or any MCP client)

```bash
crab-archi-design mcp-config --output integrations/mcp_config.json
```

The output includes a ready `codex.mcpServers` block. Register it with
`codex mcp add crab-archi-design -- crab-archi-design-mcp --stdio` (or copy the
JSON into your client config), then verify with:

```bash
crab-archi-design mcp-smoke --config integrations/mcp_config.json --strict
```

`crab-archi-design mcp-manifest` prints the full machine-readable tool catalog
(ids, args, outputs, gates, recommended sequences).

## Where artifacts land

| Artifact | Path |
| --- | --- |
| Workflow / revision reports | `projects/<id>/workflow/`, `projects/<id>/revisions/` |
| Recognition audit (mutation gate) | `projects/<id>/audits/recognition_audit_###.json` |
| Patch plan (candidate walls/openings) | `projects/<id>/patch_plans/svg_patch_plan_###.json` |
| Engine report + gates | `projects/<id>/runs/apply_edit_###/same_layer_engine_report.json` |
| Candidate drawing | `projects/<id>/alternatives/alternative_###.svg` |
| Before/after review panel | `projects/<id>/panels/review_panel_###.html` |
| Studio run reports | `projects/<id>/studio/studio_run_###/` |

## Definition of done for code changes

1. `python -m pytest -q` passes (all tests).
2. The production sample path still passes:
   `examples/run_quickstart.sh` ends with a passing release audit.
3. No proprietary drawing or project output is staged for commit.
