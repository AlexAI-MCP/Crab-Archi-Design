# Crab Archi Design

Crab Archi Design is an original-SVG-first framework for architectural community layout design automation.

OpenCrab is a required part of the workflow, not an optional reference layer.

- OpenCrab homepage: https://opencrab.sh
- OpenCrab dashboard: https://opencrab.sh/dashboard
- OpenCrab desktop: https://opencrab.sh/desktop

It is designed around the workflow proven in the Community SVG experiments:

1. Start from an original SVG drawing.
2. Recognize drawing elements, program labels, protected structures, and mutable zones.
3. Query OpenCrab MCP for the installed ontology pack, source evidence, topology, adjacency, and reusable design claims.
4. Attach standards from Excel, CSV, PDF, or ontology evidence.
5. Build a target topology manifest that links labels, room envelopes, protected geometry, standards, evidence, and constraints.
6. Compile design direction into structured intent JSON.
7. Generate native SVG alternatives with a deterministic geometry engine.
8. Let users revise alternatives by natural language or doodle sketch.
9. Run QA gates before exporting the final native SVG.

The framework intentionally keeps LLMs out of direct SVG mutation. Codex or another LLM should produce `DesignIntent` and `EditIntent` JSON. A deterministic solver should apply safe native SVG patches.

## OpenCrab MCP Contract

Every production project must use OpenCrab MCP before writing a design alternative:

1. Read the target drawing recognition IR.
2. Ask OpenCrab MCP for the relevant ontology pack and evidence-backed topology.
3. Map superior-case topology onto the target drawing's protected and mutable zones.
4. Compile an evidence-cited design intent.
5. Let the deterministic SVG solver generate only native SVG geometry.
6. Reject any candidate that violates parking, core, column, ramp, egress, or community shell constraints.

See [docs/opencrab_mcp_workflow.md](docs/opencrab_mcp_workflow.md) for the required pack and topology flow.

## Install

Python 3.9+ is supported.

```bash
pip install -e .
```

## Quickstart

Run the complete sample path from job creation to final release audit:

```bash
examples/run_quickstart.sh
```

The script creates a sample job spec, Markdown review brief, validated project package, and `release_audit_###.json` under `.quickstart-projects/`.

## CI Preflight

GitHub Actions runs the same core contract used by local handoff:

1. Install `crab-archi-design` with test dependencies.
2. Compile the CLI and reference engine.
3. Run the Python test suite.
4. Check the stdio MCP entry point with `crab-archi-design-mcp --help`.
5. Generate the MCP/OAuth exec manifest with `mcp-manifest`.
6. Generate MCP client and OAuth worker runtime config with `mcp-config`.
7. Verify the generated MCP runtime with `mcp-smoke`.
8. Generate a JSON job spec and Markdown review brief with `create-job --validate --strict-validation --brief`.
9. Validate the JSON job spec with `validate-job --strict`.
10. Execute `run-job` from a JSON job spec for the SaaS/OAuth path.
11. Execute `workflow-run` with sample SVG, standards, constraints, and OpenCrab MCP evidence.
12. Execute `revision-run` on the same project to verify the repeat-edit path.
13. Run `recognition-audit` when a target drawing needs a recognition-first gate before mutation.
14. Run `export-package`, `verify-package --strict`, `doctor --strict`, and `release-audit --strict`.

The CI sample uses:

- `examples/original_sample.svg`
- `examples/area_standard_sample.csv`
- `examples/constraint_sketch_sample.json`
- `examples/opencrab_mcp_result_sample.json`
- `examples/job_spec_sample.json`
- `examples/run_quickstart.sh`

## CLI

```bash
crab-archi-design init \
  --project-id demo \
  --source-svg /path/to/original.svg \
  --households 900 \
  --standards /path/to/area_standard.xlsx \
  --ontology-pack community_svg_topology_ontology_v2 \
  --opencrab-mcp-server opencrab \
  --engine-adapter layout-svg-engine

crab-archi-design workflow-run \
  --project-id demo \
  --source-svg /path/to/original.svg \
  --households 900 \
  --standards examples/area_standard_sample.csv \
  --ontology-pack community_svg_topology_ontology_v2 \
  --opencrab-result-file /path/to/opencrab_mcp_result.json \
  --constraint-sketch examples/constraint_sketch_sample.json \
  --prompt "Open the greenery lounge more toward the main hall and keep parking/core locked." \
  --engine-adapter layout-svg-engine \
  --skip-preview

crab-archi-design revision-run \
  --project-id demo \
  --text "Tighten the golf and sauna adjacency while keeping screen golf inside the golf cluster." \
  --sketch examples/sketch_layer_sample.json \
  --skip-preview

crab-archi-design create-job \
  --project-id demo-job \
  --source-svg examples/original_sample.svg \
  --households 900 \
  --standards examples/area_standard_sample.csv \
  --ontology-pack community_svg_topology_ontology_v2 \
  --opencrab-result-file examples/opencrab_mcp_result_sample.json \
  --constraint-sketch examples/constraint_sketch_sample.json \
  --prompt "Improve the greenery lounge hierarchy while preserving protected geometry." \
  --engine-adapter layout-svg-engine \
  --output job_specs/demo-job.json \
  --validate \
  --strict-validation \
  --skip-preview \
  --brief

crab-archi-design validate-job \
  --job job_specs/demo-job.json \
  --strict

crab-archi-design run-job \
  --job job_specs/demo-job.json \
  --strict

crab-archi-design recognize-svg \
  --project-id demo

crab-archi-design recognize-svg-v2 \
  --project-id demo

crab-archi-design standards-attach \
  --project-id demo \
  --file examples/area_standard_sample.csv \
  --households 900

crab-archi-design evidence-attach \
  --project-id demo \
  --source localcrab \
  --pack-id community_svg_topology_ontology_v2 \
  --summary "OpenCrab/LocalCrab verified the precedent topology, evidence chunks, protected zones, and 900-household program targets."

crab-archi-design opencrab-request \
  --project-id demo \
  --intent "Find evidence-backed precedent topology for the target community layout." \
  --max-results 8

crab-archi-design opencrab-sync \
  --project-id demo \
  --result-file /path/to/opencrab_mcp_result.json \
  --source-tool opencrab_search_documents

crab-archi-design constraint-attach \
  --project-id demo \
  --sketch examples/constraint_sketch_sample.json

crab-archi-design topology-build \
  --project-id demo

crab-archi-design prompt-edit \
  --project-id demo \
  --text "Open the greenery lounge more toward the main hall and keep parking/core locked."

crab-archi-design sketch-intent \
  --project-id demo \
  --sketch examples/sketch_layer_sample.json

crab-archi-design doodle-editor

crab-archi-design edit-brief \
  --project-id demo \
  --intent all

crab-archi-design project-status \
  --project-id demo

crab-archi-design design-handoff \
  --project-id demo \
  --intent all \
  --task "Prepare a native SVG community layout alternative."

crab-archi-design apply-edit \
  --project-id demo \
  --intent all \
  --skip-preview

crab-archi-design review-panel \
  --project-id demo

crab-archi-design export-package \
  --project-id demo \
  --include-source-svg

crab-archi-design verify-package \
  --zip projects/demo/exports/demo_export_001.zip \
  --strict

crab-archi-design doctor \
  --project-id demo \
  --zip projects/demo/exports/demo_export_001.zip \
  --strict

crab-archi-design release-audit \
  --project-id demo \
  --zip projects/demo/exports/demo_export_001.zip \
  --strict

crab-archi-design mcp-manifest \
  --output integrations/crab_archi_design_mcp_manifest.json

crab-archi-design mcp-config \
  --output integrations/crab_archi_design_mcp_config.json \
  --project-root projects

crab-archi-design mcp-smoke \
  --config integrations/crab_archi_design_mcp_config.json \
  --strict

crab-archi-design-mcp --stdio

crab-archi-design qa --project-id demo
```

`create-job` writes a `crab-archi-design-job-spec-v1` file from CLI, MCP, OAuth, or SaaS upload inputs. It accepts the original SVG, standards, OpenCrab MCP result files, doodle constraints, prompt, engine adapter, export policy, and optional validation flags, then prints the job spec path as the first stdout line. Use `--validate --strict-validation` to fail fast before `run-job`, and `--brief` to write a Markdown review brief next to the job spec.

`validate-job` checks a `crab-archi-design-job-spec-v1` file before execution. It verifies required project inputs, source SVG, standards, OpenCrab/evidence input, constraint sketch, engine adapter, ontology pack, and referenced local file paths, then writes `diagnostics/job_validation_###.json`.

`run-job` is the SaaS/OAuth/MCP worker entry point. It reads a validated `crab-archi-design-job-spec-v1` JSON file containing the source SVG path, standards, OpenCrab MCP result files, doodle constraints, natural-language prompt, engine adapter, and export/verification policy. It then runs `workflow-run`, `export-package`, `verify-package`, and `doctor`, and writes a `projects/<project>/jobs/job_run_###.json` report.

`workflow-run` executes the normal project path in one command: init if needed, recognize source SVG, attach standards, sync or attach evidence, attach constraints, build topology, create prompt/sketch intents, write the edit brief, status, design handoff, apply edit, review panel, final status, and a `projects/<project>/workflow/workflow_run_###.json` report.

`revision-run` executes the repeat-edit path for an existing project. It can attach an updated constraint sketch, rebuild topology, convert natural language and/or doodle sketches into edit intents, regenerate the edit brief and handoff, run the engine, create a review panel, and write `projects/<project>/revisions/revision_run_###.json`.

`apply-edit` reads structured natural-language and doodle intents plus recognition, topology, evidence, standards, and constraints, writes a `solver_input.json`, runs the configured engine adapter, copies the resulting native SVG into `projects/<project>/alternatives/`, and writes an `apply_edit_report.json`.

`svg-patch-plan` writes `projects/<project>/patch_plans/svg_patch_plan_###.json`. It is the preferred bridge from recognition into real SVG editing: it identifies existing source SVG elements by element index/tag/bbox, separates mutable wall/room candidates from locked geometry, creates same-layer opening candidates for line/path splitting, creates topology-aware endpoint-move candidates for existing line/polyline/path grip edits, anchors edits to existing program labels, and explicitly disallows zoning overlay generation. When topology contains wall-bounded `space_region` and `program_cluster` nodes, the plan prioritizes existing SVG elements that intersect those clusters so edits target the recognized original spaces first. Opening and endpoint-move candidates are scored by OpenCrab `ontology_cluster_adjacency_target` edges, so relationships such as greenery lounge to main hall are carried as evidence-backed mutation metadata instead of freehand redrawing. Production redraw engines should consume this plan and mutate existing SVG elements in place.

`same-layer-svg-engine` is the first built-in native geometry patch adapter for that production path. It consumes the latest `svg-patch-plan`, addresses existing source SVG elements by document index, collapses mutable internal partition lines, polylines, and open single-subpath M/L/H/V paths to zero length, and marks them removed in the same layer while preserving original geometry in `data-crab-original-*` attributes. With `--engine-arg=--apply-openings`, it splits selected wall lines, polylines, and open single-subpath M/L/H/V paths into before/after same-parent segments to create door openings, converting transformed polyline/path openings through world length and back into local `points`/`d` coordinates before writing. With `--engine-arg=--apply-endpoint-moves`, it moves existing line/polyline endpoints and open single-subpath M/L/H/V path endpoints by explicit world-coordinate `x/y` or `dx/dy` values, converts those values through any accumulated SVG transform back into the element's local attribute coordinates, and preserves the original coordinates in reversible attributes. Curved, arc, and closed paths are skipped for review instead of being approximated. Its QA report snapshots locked/protected candidates before mutation, skips any mutation candidate that targets a locked element, fails `locked_targets_not_selected` for that patch-plan conflict, and fails `locked_geometry_unchanged` if any protected element changes. It does not add a zoning overlay group, image, or redraw layer.

The low-level CAD-like primitive edits used by this adapter live in `solver/svg_edit_ops.py`: same-parent line/path opening splits, transform-aware path opening conversion, endpoint grip edits, source-attribute preservation, native element counting, and per-element edit capability reports. `solver/svg_mutation.py` remains the orchestration layer for candidate selection, protected-target skips, locked geometry preservation, and mutation summary reporting. The same capability checks gate opening and endpoint mutations before geometry is touched, and engine summaries include `edit_capability_summary`, `edit_capability_totals`, and review-count QA gates so unsupported primitives such as closed paths, curved paths, or non-invertible endpoint transforms are surfaced as `review_required` instead of silently degrading into overlay-style redraws.

`layout-svg-engine` is retained as a diagnostic room-envelope adapter only. It reads the community shell, mutable zone, no-go constraints, recognized column candidates, standards rows, and OpenCrab-backed intent, then creates a native SVG redraw layer with a cleanup mask for the old mutable/internal layout, program rooms, partition walls, door openings, a corridor axis, interior glazing at the lounge/hall connection, labels, preserved shell/column markup, and no raster overlay. This overlay-style candidate is not the target production workflow for high-quality architectural drawings.

`reference-svg-engine` remains available as a diagnostic adapter. It consumes the same solver input and emits a native SVG candidate plus engine report, using only additive SVG elements and no raster overlay.

`recognize-svg` writes `projects/<project>/recognition/recognition_manifest.json`. It stores SVG parse status, viewBox, primitive counts, primitive bounding boxes, column candidates, wall candidates, room-envelope candidates, label candidates, and program role hints before any layout mutation.

`recognize-svg-v2` writes `projects/<project>/recognition/recognition_ir_v2.json`. It uses the modular parser stack (`safe_load`, `namespace`, `defs`, `style`, `units`, `transform`, `shapes`, `path`, `recognition/classify`) to normalize SVG primitives into world coordinates. This path skips raw `<defs>` geometry, expands placed `<use>`/`<symbol>` instances into their actual drawing coordinates, supports nested affine transforms including skew, and keeps path geometry, inherited style, labels, raster detection, and role hints in a single IR instead of asking an LLM to redraw geometry.

`topology-build` writes `projects/<project>/topology/topology_manifest.json`. It turns recognition candidates, standards rows, OpenCrab evidence, and doodle constraints into an explicit node/edge graph for labels, room envelopes, columns, walls, program standards, protected constraints, and ontology adjacency targets. When `recognition_ir_v2.json` exists and is active, topology uses it as the preferred recognition source so path geometry, transform-normalized labels, and column candidates become graph nodes. It also creates `space_region` nodes when a label can be bounded by nearby wall axes; these are recognition evidence for existing program spaces, not a generated zoning overlay. `qa`, `edit-brief`, `project-status`, `design-handoff`, and `apply-edit` require this manifest to be active before a final SVG alternative can pass.

`recognition-audit` writes `projects/<project>/audits/recognition_audit_###.json`. It is the recognition-first gate for real architectural drawings: program labels must have coordinates, wall and column candidates must be detected, the community shell/mutable zone/protected no-go zones must be confirmed, and topology must connect labels to room envelopes or wall-bounded `space_region` nodes. The audit reads IR v2 metrics first when available, falling back to the legacy recognition manifest for older projects. If this audit is `review_required`, the correct next step is to improve recognition or user-confirmed constraints, not to generate another zoning block.

`standards-attach` writes `projects/<project>/standards/standards_manifest.json`. CSV files are parsed into selected rows for the household count; Excel, PDF, and JSON files are attached as verified standards references for the engine adapter.

`evidence-attach` writes `projects/<project>/evidence/evidence_manifest.json`. `qa` and `apply-edit` require this manifest to be verified before a final SVG alternative can pass.

`opencrab-sync` normalizes JSON returned by OpenCrab MCP tools such as `opencrab_query` and `opencrab_search_documents`. It writes `projects/<project>/opencrab/opencrab_sync_###.json` and appends the normalized evidence to `projects/<project>/evidence/evidence_manifest.json`.

`opencrab-request` writes `projects/<project>/opencrab/opencrab_request_###.json` and `.md`. It packages the recommended OpenCrab MCP tool call, query, expected result file, and next `opencrab-sync` command from the current project context.

`constraint-attach` writes `projects/<project>/constraints/constraint_manifest.json`. Use it for community shell, parking/core/column/ramp no-go edges, lock boundaries, mutable zones, and projectable zones. `qa` and `apply-edit` require an active constraint manifest before a final SVG alternative can pass.

`doodle-editor` prints the local SVG doodle editor path and `file://` URL. The editor loads a source SVG from your machine, records vector strokes in source viewBox coordinates, and downloads sketch JSON for `sketch-intent`.

`edit-brief` summarizes natural-language and doodle intents before SVG mutation. It writes JSON and Markdown briefs, checks OpenCrab evidence, and flags doodle strokes outside the source SVG viewBox.

`project-status` writes `projects/<project>/status/project_status.json`. It summarizes readiness gates, latest briefs, latest apply reports, latest alternatives, latest review panels, and metrics such as recognized primitive count, program labels, topology nodes/edges, evidence count, standards rows, constraints, and edit intents.

`design-handoff` writes `projects/<project>/handoffs/design_handoff_###.json` and `.md`. It packages the current status gates, source recognition, topology graph summary, OpenCrab evidence, standards excerpts, constraints, natural-language and doodle intents, prompt blocks, and deterministic engine contract for Codex/LLM/MCP-backed design generation.

`review-panel` generates a local before/after HTML panel with original SVG, alternative SVG, intent summary, recognition/topology summaries, apply checks, and engine QA gates.

`export-package` writes `projects/<project>/exports/export_manifest_###.json` and `projects/<project>/exports/<project>_export_###.zip`. The ZIP bundles the latest status, manifests, OpenCrab sync results, edit intents, handoff, workflow report, apply report, engine reports, alternative SVG, and review panel for downstream agents or SaaS upload. Source SVG inclusion is opt-in with `--include-source-svg`.

`verify-package` validates an exported ZIP before handoff. It checks ZIP integrity, the embedded export manifest, required files, archive membership, file sizes, and SHA-256 hashes. Use `--check-local-files` when validating on the same machine that produced the package.

`doctor` writes `projects/<project>/diagnostics/doctor_report_###.json`. It diagnoses the local framework install, OpenCrab MCP configuration, project readiness gates, latest native SVG candidate, and optional export ZIP verification in one report. Use `--strict` in CI, OAuth upload flows, or MCP handoffs.

`release-audit` writes `projects/<project>/audits/release_audit_###.json`. It is the final handoff gate: it checks project candidate readiness, OpenCrab evidence, topology/standards/constraints, native SVG output, package verification, and doctor diagnostics in one report.

`mcp-manifest` writes or prints a machine-readable tool catalog for Codex exec, MCP wrappers, OAuth upload workers, and SaaS ingestion services. It describes each CLI tool id, subcommand, required arguments, outputs, gates, OpenCrab MCP requirement, recommended command sequences, and security boundaries.

`mcp-config` writes or prints runtime configuration for MCP clients and OAuth workers, including the `crab-archi-design-mcp --stdio` command, default `CRAB_ARCHI_PROJECT_ROOT`, Codex-style `mcpServers` JSON, smoke-test messages, and worker preflight commands.

`mcp-smoke` starts the configured stdio MCP server and verifies `initialize` plus `tools/list`. It writes `diagnostics/mcp_smoke_report_###.json` and can run with `--strict` in CI or before OAuth/SaaS deployment.

`crab-archi-design-mcp` starts a dependency-free stdio JSON-RPC bridge. It supports MCP `initialize`, `tools/list`, and `tools/call`, then maps tool calls back to the tested CLI commands.

For the full revision loop, see [docs/edit_loop.md](docs/edit_loop.md).

## Repository Scope

This repository contains the reusable framework shell, schemas, and orchestration layer. It does not include proprietary SVG drawings, project outputs, or OpenCrab-saas internal artifacts.

## Core Principle

```text
Original SVG
  -> Recognition IR v2
  -> Safe SVG load / style / units / transform normalization
  -> Shape and path flattening
  -> Role classification
  -> Recognition Manifest
  -> Constraint Graph
  -> OpenCrab MCP Ontology Evidence
  -> OpenCrab Sync
  -> Standards Evidence
  -> Standards Manifest
  -> Design Intent JSON
  -> Workflow Run
  -> Edit Brief
  -> Project Status
  -> Design Handoff
  -> Native SVG Solver
  -> QA
  -> Export Package
  -> Doctor
  -> MCP/OAuth Tool Manifest
  -> Natural Language / Doodle Edit Loop
```

## Status

This is the first clean framework extraction. The deterministic recognition/redraw engine is expected to be connected through adapters or MCP tools.
