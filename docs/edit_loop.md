# Natural Language and Doodle Edit Loop

Crab Archi Design separates user direction from SVG mutation.

Natural language and doodles are first converted into structured intent JSON. A deterministic engine adapter then reads the intent, OpenCrab evidence, standards, and drawing constraints before writing native SVG geometry.

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

## Apply the Edit

```bash
crab-archi-design --project-root projects apply-edit \
  --project-id a801-802-opencrab-test \
  --intent all \
  --skip-preview
```

`apply-edit` performs the handoff:

1. Selects the requested edit intents.
2. Loads `evidence/evidence_manifest.json`.
3. Writes `runs/apply_edit_###/solver_input.json`.
4. Runs the project `engine_adapter`.
5. Discovers native SVG/report/preview outputs.
6. Ignores the original source SVG when choosing the candidate.
7. Copies the generated SVG to `alternatives/alternative_###.svg`.
8. Writes `runs/apply_edit_###/apply_edit_report.json`.

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

## Practical Revision Pattern

For architectural layout revisions, use this order:

1. Natural language: describe the intent and constraints.
2. Doodle: mark the exact edge, room, circulation line, or wall segment.
3. `apply-edit`: generate a native SVG candidate.
4. `review-panel`: inspect before/after.
5. Repeat with another short prompt or doodle repair intent.

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
