# OpenCrab MCP Workflow

OpenCrab MCP is the required knowledge path for Crab Archi Design.

- Homepage: https://opencrab.sh
- Dashboard: https://opencrab.sh/dashboard
- Desktop: https://opencrab.sh/desktop

## Required Flow

1. Load the original SVG and build recognition IR.
2. Classify protected geometry: parking, parking count, columns, cores, ramps, stairs, egress, wet cores, machine rooms, and outer shell.
3. Classify mutable community zones: rooms, partitions, openings, program labels, secondary circulation, and finish intent.
4. Attach user-confirmed community shell, no-go, lock, mutable, and projectable constraints.
5. Query OpenCrab MCP for the selected ontology pack.
6. Retrieve precedent topology, program hierarchy, adjacency levers, area standards, claims, and evidence references.
7. Project the superior-case ontology onto the target drawing's mutable zones.
8. Compile a `DesignIntent` JSON with OpenCrab evidence references.
9. Compile an `edit-brief` from natural-language and doodle intents before geometry mutation.
10. Generate native SVG geometry through a deterministic solver.
11. Run QA for no-go intrusion, lock-zone intrusion, area compliance, topology preservation, and native-SVG-only output.
12. Accept natural-language or doodle revisions, then repeat from the OpenCrab evidence projection step.

## Design Rule

The agent may explain options before querying OpenCrab MCP, but it must not produce a final layout alternative until OpenCrab ontology evidence has been attached to the project manifest and design intent.

## Evidence Gate

`evidence-attach` records the OpenCrab/LocalCrab pack, query, summary, source file, and metadata in:

```text
projects/<project>/evidence/evidence_manifest.json
```

`qa` and `apply-edit` treat the candidate as `review_required` until that manifest has `status: verified`. The `opencrab_evidence_verified` check must pass before a generated SVG can be treated as an evidence-backed alternative.

`edit-brief` should be run after natural-language or doodle input and before `apply-edit`. It does not replace OpenCrab MCP. It confirms the OpenCrab evidence gate, summarizes the requested operations, and catches basic drawing-coordinate mistakes such as doodle strokes outside the source SVG viewBox.

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
