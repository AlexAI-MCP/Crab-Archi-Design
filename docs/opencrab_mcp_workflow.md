# OpenCrab MCP Workflow

OpenCrab MCP is the required knowledge path for Crab Archi Design.

- Homepage: https://opencrab.sh
- Dashboard: https://opencrab.sh/dashboard
- Desktop: https://opencrab.sh/desktop

## Required Flow

1. Load the original SVG and build recognition IR.
2. Attach a source recognition manifest with primitive counts, labels, and program role hints.
3. Classify protected geometry: parking, parking count, columns, cores, ramps, stairs, egress, wet cores, machine rooms, and outer shell.
4. Classify mutable community zones: rooms, partitions, openings, program labels, secondary circulation, and finish intent.
5. Attach household-count standards and selected area/program rows.
6. Attach user-confirmed community shell, no-go, lock, mutable, and projectable constraints.
7. Query OpenCrab MCP for the selected ontology pack.
8. Retrieve precedent topology, program hierarchy, adjacency levers, area standards, claims, and evidence references.
9. Project the superior-case ontology onto the target drawing's mutable zones.
10. Compile a `DesignIntent` JSON with OpenCrab evidence references.
11. Compile an `edit-brief` from natural-language and doodle intents before geometry mutation.
12. Run `project-status` to confirm recognition, standards, OpenCrab evidence, constraints, and edit intents are ready.
13. Generate native SVG geometry through a deterministic solver.
14. Run QA for no-go intrusion, lock-zone intrusion, area compliance, topology preservation, and native-SVG-only output.
15. Run `project-status` again to confirm the latest candidate and review panel are complete.
16. Accept natural-language or doodle revisions, then repeat from the OpenCrab evidence projection step.

## Design Rule

The agent may explain options before querying OpenCrab MCP, but it must not produce a final layout alternative until OpenCrab ontology evidence has been attached to the project manifest and design intent.

## Evidence Gate

`evidence-attach` records the OpenCrab/LocalCrab pack, query, summary, source file, and metadata in:

```text
projects/<project>/evidence/evidence_manifest.json
```

`qa` and `apply-edit` treat the candidate as `review_required` until that manifest has `status: verified`. The `opencrab_evidence_verified` check must pass before a generated SVG can be treated as an evidence-backed alternative.

`edit-brief` should be run after natural-language or doodle input and before `apply-edit`. It does not replace OpenCrab MCP. It confirms the OpenCrab evidence gate, summarizes the requested operations, and catches basic drawing-coordinate mistakes such as doodle strokes outside the source SVG viewBox.

`project-status` writes `projects/<project>/status/project_status.json` as the command-center artifact for these gates. Use it before solver handoff and after candidate review so the agent can distinguish `ready_for_apply`, `candidate_review_required`, and `complete_candidate_ready` states.

## Recognition Gate

`recognize-svg` records source drawing recognition in:

```text
projects/<project>/recognition/recognition_manifest.json
```

The recognition manifest is the first project-specific drawing IR. It stores source SVG parse status, viewBox, primitive counts, raster image detection, text label candidates, and program role hints. `qa`, `edit-brief`, and `apply-edit` treat a candidate as `review_required` until this manifest has `status: active`.

## Standards Gate

`standards-attach` records household-count standards in:

```text
projects/<project>/standards/standards_manifest.json
```

The standards manifest is the bridge between area criteria and geometry generation. For CSV files, selected rows are embedded in the manifest. For Excel, PDF, and JSON files, the file reference is verified and passed to the engine adapter. `qa`, `edit-brief`, and `apply-edit` treat a candidate as `review_required` until this manifest has `status: active`.

## Constraint Gate

`constraint-attach` records user-confirmed drawing constraints in:

```text
projects/<project>/constraints/constraint_manifest.json
```

The constraint manifest is the bridge between drawing recognition and user correction. It should include the community shell, protected no-go areas, locked geometry, mutable areas, and projectable zones. `qa`, `edit-brief`, and `apply-edit` treat a candidate as `review_required` until this manifest has `status: active`.

## Community Layout Pack Expectations

The community design ontology pack should expose:

- Program nodes: greenery lounge, fitness, GX, golf, screen golf, sauna, locker, shower, office, management, support rooms, toilets, hall, corridor, and storage.
- Topology edges: adjacency, visual connection, acoustic separation, wet-zone grouping, main-entry connection, protected-zone exclusion, and service access.
- Evidence nodes: source SVG file, room area, room position, shell relationship, section or ceiling-height evidence, circulation evidence, precedent claim, and standard reference.
- Constraint nodes: no-go zones, lock zones, mutable zones, minimum corridor width, area target by household count, and parking-count preservation.
