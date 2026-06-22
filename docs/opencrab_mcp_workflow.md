# OpenCrab MCP Workflow

OpenCrab MCP is the required knowledge path for Crab Archi Design.

- Homepage: https://opencrab.sh
- Dashboard: https://opencrab.sh/dashboard
- Desktop: https://opencrab.sh/desktop

## Required Flow

The full flow can be run manually step by step, through `workflow-run`, or through `run-job` once the source SVG, standards, OpenCrab MCP result JSON, constraints, and prompt are available.

1. Load the original SVG and build recognition IR.
2. Attach a source recognition manifest with primitive counts, labels, and program role hints.
3. Classify protected geometry: parking, parking count, columns, cores, ramps, stairs, egress, wet cores, machine rooms, and outer shell.
4. Classify mutable community zones: rooms, partitions, openings, program labels, secondary circulation, and finish intent.
5. Attach household-count standards and selected area/program rows.
6. Attach user-confirmed community shell, no-go, lock, mutable, and projectable constraints.
7. Query OpenCrab MCP for the selected ontology pack.
8. Retrieve precedent topology, program hierarchy, adjacency levers, area standards, claims, and evidence references.
9. Run `opencrab-sync` to normalize MCP results into the project evidence manifest.
10. Build the target topology manifest from recognition, standards, OpenCrab evidence, and constraints.
11. Project the superior-case ontology onto the target drawing's mutable zones.
12. Compile a `DesignIntent` JSON with OpenCrab evidence references.
13. Compile an `edit-brief` from natural-language and doodle intents before geometry mutation.
14. Run `project-status` to confirm recognition, topology, standards, OpenCrab evidence, constraints, and edit intents are ready.
15. Build a `design-handoff` package for Codex, an LLM wrapper, an MCP tool, or the deterministic solver.
16. Generate native SVG geometry through a deterministic solver.
17. Run QA for no-go intrusion, lock-zone intrusion, area compliance, topology preservation, and native-SVG-only output.
18. Run `project-status` again to confirm the latest candidate and review panel are complete.
19. Run `export-package` to bundle the evidence-backed candidate and review artifacts.
20. Run `verify-package` to validate the exported ZIP before handoff or upload.
21. Run `doctor` to diagnose local CLI readiness, OpenCrab configuration, project gates, candidate readiness, and optional package verification.
22. Use `run-job` when a SaaS, OAuth, or MCP worker needs to execute the whole sequence from a single JSON job spec.
23. Run `mcp-manifest`, `mcp-config`, and `mcp-smoke` when a Codex exec runner, MCP wrapper, OAuth worker, or SaaS ingestion layer needs a machine-readable tool catalog, runtime configuration, and connection smoke test.
24. Accept natural-language or doodle revisions through `revision-run`, then export and verify the package again.

## Design Rule

The agent may explain options before querying OpenCrab MCP, but it must not produce a final layout alternative until OpenCrab ontology evidence has been attached to the project manifest and design intent.

## Evidence Gate

`evidence-attach` records the OpenCrab/LocalCrab pack, query, summary, source file, and metadata in:

```text
projects/<project>/evidence/evidence_manifest.json
```

`qa` and `apply-edit` treat the candidate as `review_required` until that manifest has `status: verified`. The `opencrab_evidence_verified` check must pass before a generated SVG can be treated as an evidence-backed alternative.

`opencrab-sync` is the preferred bridge when the evidence came directly from OpenCrab MCP. It records the raw MCP result in:

```text
projects/<project>/opencrab/opencrab_sync_###.json
```

Then it appends normalized `opencrab_query` or `opencrab_search_documents` evidence to the same evidence manifest used by `qa`, `edit-brief`, `project-status`, `design-handoff`, and `apply-edit`.

`workflow-run` can call `opencrab-sync` as part of the full sequence when `--opencrab-result-file` or `--opencrab-result-json` is supplied. The workflow report records whether each required gate passed, failed, or was skipped.

`topology-build` runs after recognition, standards, evidence, and constraints are available. It writes:

```text
projects/<project>/topology/topology_manifest.json
```

The topology manifest is the target graph used for projection. It links recognized program labels, room envelopes, protected columns, wall candidates, standards program roles, user constraints, and OpenCrab adjacency targets. `qa`, `edit-brief`, `project-status`, `design-handoff`, and `apply-edit` treat a project as `review_required` until this graph has `status: active`.

`run-job` wraps the OpenCrab-backed workflow for product integrations. The job spec should include the OpenCrab MCP result file paths, constraint sketch JSON, source SVG, standards, prompt, and strict verification settings. The job report records the workflow report, export ZIP, verification report, and doctor report paths.

`edit-brief` should be run after natural-language or doodle input and before `apply-edit`. It does not replace OpenCrab MCP. It confirms the OpenCrab evidence gate, summarizes the requested operations, and catches basic drawing-coordinate mistakes such as doodle strokes outside the source SVG viewBox.

`project-status` writes `projects/<project>/status/project_status.json` as the command-center artifact for these gates. Use it before solver handoff and after candidate review so the agent can distinguish `ready_for_apply`, `candidate_review_required`, and `complete_candidate_ready` states.

`design-handoff` writes `projects/<project>/handoffs/design_handoff_###.json` and `.md`. It packages the prompt blocks, topology graph summary, OpenCrab evidence summaries, standards excerpts, constraints, recognized drawing context, and deterministic engine contract after the readiness gates are satisfied.

`layout-svg-engine` can be used at the solver step to generate a standards-backed room-envelope candidate without raster overlays. It uses the attached community shell, mutable zone, no-go constraints, recognized column candidates, OpenCrab evidence gate, and standards rows to draw native SVG room partitions for greenery lounge, fitness, golf, wellness, hall, and support programs.

`reference-svg-engine` can still be used to validate the full workflow without changing room envelopes. It produces a native SVG reference candidate and report for diagnostics.

`export-package` is the portable output boundary for downstream systems. It bundles the latest OpenCrab-backed evidence, project status, design handoff, workflow report, apply report, native SVG candidate, and review panel. Include the source SVG only when the receiving environment is allowed to access the original drawing.

`verify-package` is the receiving-side safety check. It confirms the ZIP can be opened, contains the embedded export manifest and all expected artifacts, and that archived files match the recorded hashes.

`doctor` is the final operational check before a project moves into CI, GitHub handoff, OAuth upload, SaaS ingestion, or another MCP agent. It writes:

```text
projects/<project>/diagnostics/doctor_report_###.json
```

The report combines local tool checks, OpenCrab MCP manifest checks, `project-status` readiness gates, latest native SVG candidate checks, and optional `verify-package` results. `release-audit` then combines those doctor diagnostics with package verification and OpenCrab-backed project gates into the final handoff report.

`mcp-manifest` is the integration catalog. It advertises the OpenCrab evidence gate, CLI subcommands, required arguments, output artifacts, recommended command sequences, and security rules for MCP/OAuth execution. The first-run worker path is `create-job -> validate-job -> run-job`, with OpenCrab MCP results passed into `create-job` as evidence files. `mcp-config` emits the runtime server config, and `mcp-smoke` verifies the configured server. `crab-archi-design-mcp --stdio` serves the same tools through `tools/list` and `tools/call`. See:

```text
docs/mcp_oauth_integration.md
```

## Recognition Gate

`recognize-svg` records source drawing recognition in:

```text
projects/<project>/recognition/recognition_manifest.json
```

The recognition manifest is the first project-specific drawing IR. It stores source SVG parse status, viewBox, primitive counts, primitive bounding boxes, column candidates, wall candidates, room-envelope candidates, raster image detection, text label candidates, and program role hints. `qa`, `edit-brief`, and `apply-edit` treat a candidate as `review_required` until this manifest has `status: active`.

## Topology Gate

`topology-build` records the target drawing topology in:

```text
projects/<project>/topology/topology_manifest.json
```

The topology manifest is the graph that lets the superior-case ontology be projected onto the target drawing without directly drawing over protected geometry. It is built from recognition candidates, standards rows, OpenCrab evidence, and user-confirmed constraints. `qa`, `edit-brief`, `project-status`, `design-handoff`, `apply-edit`, `export-package`, and `doctor` all treat it as a required final-SVG artifact.

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
