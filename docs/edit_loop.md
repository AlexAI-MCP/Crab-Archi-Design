# Natural Language and Doodle Edit Loop

Crab Archi Design separates user direction from SVG mutation.

Natural language and doodles are first converted into structured intent JSON. A deterministic engine adapter then reads the intent, OpenCrab evidence, standards, and drawing constraints before writing native SVG geometry.

## One Command Workflow

Use `workflow-run` when you want the standard path to run from source SVG to reviewable alternative in one command:

```bash
crab-archi-design --project-root projects workflow-run \
  --project-id a801-802-opencrab-test \
  --source-svg "/path/to/original.svg" \
  --households 900 \
  --standards "/path/to/area_standard.csv" \
  --ontology-pack community_svg_topology_ontology_v2 \
  --opencrab-result-file /path/to/opencrab_mcp_result.json \
  --constraint-sketch /path/to/constraint_sketch.json \
  --prompt "Open the greenery lounge toward the main hall while preserving parking, cores, columns, ramps, and the community shell." \
  --engine-adapter reference-svg-engine \
  --skip-preview
```

This creates a workflow report:

```text
projects/a801-802-opencrab-test/workflow/workflow_run_###.json
```

The command runs the same gates as the manual loop: source recognition, standards, OpenCrab evidence sync, constraints, intent, edit brief, project status, design handoff, apply edit, review panel, and final project status. Existing project manifests can be reused; pass `--reinit` only when you intentionally want to recreate the project manifest.

## Recognize Source SVG

Start by converting the original SVG into a lightweight recognition manifest:

```bash
crab-archi-design --project-root projects recognize-svg \
  --project-id a801-802-opencrab-test
```

This creates:

```text
projects/a801-802-opencrab-test/recognition/recognition_manifest.json
```

The manifest stores SVG parse status, viewBox, primitive counts, raster image detection, text label candidates, and program role hints. `qa`, `edit-brief`, and `apply-edit` report `review_required` until this recognition manifest is active.

## Attach OpenCrab Evidence

Every edit loop should start with verified ontology evidence. Natural language and doodles can steer the edit, but the solver should not pass a final alternative until OpenCrab/LocalCrab evidence is attached.

```bash
crab-archi-design --project-root projects evidence-attach \
  --project-id a801-802-opencrab-test \
  --source localcrab \
  --pack-id community_svg_topology_ontology_v2 \
  --query "community SVG topology, 900 household standards, protected zones, precedent adjacency" \
  --summary "LocalCrab verified the precedent community topology, evidence chunks, protected geometry, mutable zones, and 900-household program targets."
```

This creates:

```text
projects/a801-802-opencrab-test/evidence/evidence_manifest.json
```

`qa` reports `review_required` until this evidence manifest is verified.

## Sync OpenCrab MCP Results

When Codex or another agent calls OpenCrab MCP directly, save the returned JSON and normalize it into the project evidence gate:

```bash
crab-archi-design --project-root projects opencrab-sync \
  --project-id a801-802-opencrab-test \
  --result-file /path/to/opencrab_mcp_result.json \
  --source-tool opencrab_search_documents
```

You can also pass inline JSON with `--result-json` or pipe JSON through stdin. The command creates:

```text
projects/a801-802-opencrab-test/opencrab/opencrab_sync_###.json
projects/a801-802-opencrab-test/evidence/evidence_manifest.json
```

`opencrab-sync` understands common OpenCrab MCP result shapes, including `answer` plus `evidence` from `opencrab_query` and evidence arrays from `opencrab_search_documents`. The normalized evidence is then available to `edit-brief`, `project-status`, `design-handoff`, and `apply-edit`.

## Attach Standards

Attach the area, program, or finish standards before running a layout alternative. If no `--file` is supplied, the command uses the files passed to `init --standards`.

```bash
crab-archi-design --project-root projects standards-attach \
  --project-id a801-802-opencrab-test \
  --households 900
```

For an explicit file:

```bash
crab-archi-design --project-root projects standards-attach \
  --project-id a801-802-opencrab-test \
  --file "/path/to/area_standard.csv" \
  --households 900
```

This creates:

```text
projects/a801-802-opencrab-test/standards/standards_manifest.json
```

CSV files are parsed and selected rows are attached to the solver input. Excel, PDF, and JSON standards are verified and attached as source references for the engine adapter. `qa`, `edit-brief`, and `apply-edit` report `review_required` until this standards manifest is active.

## Attach Drawing Constraints

Use the doodle editor to mark the community shell, no-go zones, lock boundaries, and mutable zones. Then attach that sketch as project constraints:

```bash
crab-archi-design --project-root projects constraint-attach \
  --project-id a801-802-opencrab-test \
  --sketch /path/to/constraint_sketch.json
```

This creates:

```text
projects/a801-802-opencrab-test/constraints/constraint_manifest.json
```

Recommended modes:

- `community_shell`: the outer community boundary that alternatives must not cross.
- `no_go_zone`: parking, ramp, core, stair, column, equipment, or egress areas that must not be invaded.
- `lock_boundary`: existing walls, doors, or geometry that should be preserved.
- `mutable_zone`: areas where internal partitions and program layouts may change.
- `projectable_zone`: zones where precedent topology may be projected.

`qa`, `edit-brief`, and `apply-edit` report `review_required` until this constraint manifest is active.

## Natural Language

```bash
crab-archi-design --project-root projects prompt-edit \
  --project-id a801-802-opencrab-test \
  --text "Open the greenery lounge toward the main hall, keep parking/core/columns locked, and keep screen golf inside the golf cluster."
```

This creates:

```text
projects/a801-802-opencrab-test/edit_intents/prompt_edit_###.json
```

## Doodle Sketch

Doodles should be stored as vector strokes in the source SVG coordinate space.

Open the local editor:

```bash
crab-archi-design doodle-editor
```

The editor is also available directly at:

```text
tools/doodle_editor.html
```

In the editor, load the original SVG, draw only the change intent, then export the sketch JSON. Recommended stroke modes are:

- `lock_boundary`: mark community shell, parking edge, core, column, ramp, stair, or other no-go edges.
- `open_connection`: mark where two programs should visually or physically connect.
- `program_shift`: mark a room or cluster that should move inside the mutable community shell.
- `partition_rework`: mark internal wall lines that may be redrawn.

```json
{
  "coordinate_space": "source_svg_viewbox",
  "strokes": [
    {
      "stroke_id": "s001",
      "mode": "open_connection",
      "target_hint": "greenery_lounge_to_main_hall",
      "points": [[4200, 2300], [5200, 2300]]
    },
    {
      "stroke_id": "s002",
      "mode": "lock_boundary",
      "target_hint": "parking_no_go_edge",
      "points": [[6100, 3800], [7600, 4600]]
    }
  ]
}
```

Then run:

```bash
crab-archi-design --project-root projects sketch-intent \
  --project-id a801-802-opencrab-test \
  --sketch /path/to/downloaded_sketch.json
```

Natural language and doodles can be combined. For example, use natural language to state the design rule, then use a doodle to point to the exact wall, door, or corridor segment.

## Review the Edit Brief

Before mutating SVG geometry, compile the current natural-language and doodle intents into a reviewable brief:

```bash
crab-archi-design --project-root projects edit-brief \
  --project-id a801-802-opencrab-test \
  --intent all
```

This creates:

```text
projects/a801-802-opencrab-test/briefs/edit_brief_###.json
projects/a801-802-opencrab-test/briefs/edit_brief_###.md
```

The brief checks whether source recognition is active, whether OpenCrab evidence is verified, whether standards and constraints are active, whether the original SVG parses, whether sketch source files are available, and whether all doodle points stay inside the source SVG viewBox. If a user doodles outside the target drawing area, the brief returns `review_required` before the solver touches SVG geometry.

## Check Project Status

Use `project-status` whenever you need a command-center view of the current project gates and latest artifacts:

```bash
crab-archi-design --project-root projects project-status \
  --project-id a801-802-opencrab-test
```

This creates:

```text
projects/a801-802-opencrab-test/status/project_status.json
```

The status file reports whether the project is `review_required`, `ready_for_apply`, `candidate_review_required`, or `complete_candidate_ready`. It also records the latest recognition, standards, evidence, constraint, brief, apply report, alternative SVG, and review panel paths. Use it before `apply-edit` to confirm all required gates are active, and after `review-panel` to confirm the latest candidate is a native SVG with no raster overlay.

## Build the Design Handoff

Before calling a geometry engine, package the project into a handoff that Codex, an LLM wrapper, an MCP tool, or a deterministic solver can read:

```bash
crab-archi-design --project-root projects design-handoff \
  --project-id a801-802-opencrab-test \
  --intent all \
  --task "Prepare a native SVG community layout alternative using the OpenCrab ontology and 900-household standards."
```

This creates:

```text
projects/a801-802-opencrab-test/handoffs/design_handoff_###.json
projects/a801-802-opencrab-test/handoffs/design_handoff_###.md
```

The handoff includes current readiness gates, source recognition summary, OpenCrab evidence summaries, standards excerpts, constraint summaries, natural-language and doodle operations, prompt blocks, and the required engine output contract. It reports `review_required` unless the project is ready for solver handoff and the latest `edit-brief` has passed.

## Apply the Edit

```bash
crab-archi-design --project-root projects apply-edit \
  --project-id a801-802-opencrab-test \
  --intent all \
  --skip-preview
```

For a built-in end-to-end smoke test, initialize the project with `--engine-adapter reference-svg-engine` or pass it at runtime:

```bash
crab-archi-design --project-root projects apply-edit \
  --project-id a801-802-opencrab-test \
  --intent all \
  --engine-adapter reference-svg-engine \
  --skip-preview
```

The reference engine writes a native SVG candidate with an additive reference layer and an engine report. It is not the final architectural redraw engine; use it to verify that recognition, OpenCrab evidence, standards, constraints, intents, solver handoff, artifact discovery, QA, and review panels all connect correctly.

`apply-edit` performs the handoff:

1. Selects the requested edit intents.
2. Loads `recognition/recognition_manifest.json`.
3. Loads `evidence/evidence_manifest.json`.
4. Loads `standards/standards_manifest.json`.
5. Loads `constraints/constraint_manifest.json`.
6. Writes `runs/apply_edit_###/solver_input.json`.
7. Runs the project `engine_adapter`.
8. Discovers native SVG/report/preview outputs.
9. Ignores the original source SVG when choosing the candidate.
10. Copies the generated SVG to `alternatives/alternative_###.svg`.
11. Writes `runs/apply_edit_###/apply_edit_report.json`.

## Review the Alternative

```bash
crab-archi-design --project-root projects review-panel \
  --project-id a801-802-opencrab-test
```

This creates:

```text
projects/a801-802-opencrab-test/panels/review_panel_###.html
```

The panel embeds the original SVG and generated alternative SVG side by side, then shows the intent summary, apply checks, engine QA gates, and SVG inspection counts.

## Export the Package

When the candidate is ready for another agent, a GitHub handoff, or a SaaS/OAuth upload flow, package the latest artifacts:

```bash
crab-archi-design --project-root projects export-package \
  --project-id a801-802-opencrab-test
```

This creates:

```text
projects/a801-802-opencrab-test/exports/export_manifest_###.json
projects/a801-802-opencrab-test/exports/a801-802-opencrab-test_export_###.zip
```

By default, the package includes latest project status, recognition, evidence, standards, constraints, edit intents, OpenCrab sync artifacts, design handoff, workflow report, apply report, engine reports, alternative SVG, and review panel. The original source SVG is excluded unless you pass `--include-source-svg`, which is useful for local handoff but should be deliberate for proprietary drawings.

## Practical Revision Pattern

For architectural layout revisions, use this order:

1. `workflow-run`: use this for the normal full path when all inputs are ready.
2. `recognize-svg`: attach source SVG parse, primitives, and labels.
3. `standards-attach`: attach household-count standards and selected rows.
4. `evidence-attach` or `opencrab-sync`: attach OpenCrab/LocalCrab ontology evidence.
5. `constraint-attach`: convert shell/no-go/mutable doodles into enforced project constraints.
6. Natural language: describe the design intent and constraints.
7. Doodle: mark the exact edge, room, circulation line, or wall segment.
8. `edit-brief`: check recognition, evidence, standards, constraints, source SVG parse, and sketch bounds.
9. `project-status`: confirm the project is ready for solver handoff.
10. `design-handoff`: package the current evidence, standards, constraints, and prompt blocks.
11. `apply-edit`: generate a native SVG candidate.
12. `review-panel`: inspect before/after.
13. `project-status`: confirm the latest candidate and review artifacts are complete.
14. `export-package`: bundle the latest artifacts for handoff.
15. Repeat with another short prompt or doodle repair intent.

## Safety Order

The solver should apply instructions in this priority:

```text
1. No-go and lock constraints: parking, columns, cores, ramps, stairs, egress, community shell
2. OpenCrab ontology evidence and topology
3. Area standards by household count
4. Doodle edit intent
5. Natural-language edit intent
6. Spatial quality and finish direction
```

## Adapter Environment

Engine adapters receive these environment variables:

- `CRAB_ARCHI_SOLVER_INPUT`
- `CRAB_ARCHI_RUN_DIR`
- `CRAB_ARCHI_PROJECT_DIR`
- `CRAB_ARCHI_PROJECT_ID`
- `CRAB_ARCHI_SOURCE_SVG`
- `CRAB_ARCHI_INTENTS`

Adapters should print or report the generated SVG path. If multiple SVGs are discovered, `apply-edit` excludes the original source SVG and copies the first generated candidate.
