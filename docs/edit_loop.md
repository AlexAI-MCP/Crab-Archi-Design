# Natural Language and Doodle Edit Loop

Crab Archi Design separates user direction from SVG mutation.

Natural language and doodles are first converted into structured intent JSON. A deterministic engine adapter then reads the intent, OpenCrab evidence, standards, and drawing constraints before writing native SVG geometry.

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
  --sketch examples/sketch_layer_sample.json
```

## Apply the Edit

```bash
crab-archi-design --project-root projects apply-edit \
  --project-id a801-802-opencrab-test \
  --intent all \
  --skip-preview
```

`apply-edit` performs the handoff:

1. Selects the requested edit intents.
2. Writes `runs/apply_edit_###/solver_input.json`.
3. Runs the project `engine_adapter`.
4. Discovers native SVG/report/preview outputs.
5. Ignores the original source SVG when choosing the candidate.
6. Copies the generated SVG to `alternatives/alternative_###.svg`.
7. Writes `runs/apply_edit_###/apply_edit_report.json`.

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
