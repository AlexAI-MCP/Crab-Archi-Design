# Framework Notes

Crab Archi Design is organized around four layers. OpenCrab MCP is mandatory because the design agent must retrieve ontology-backed evidence before proposing geometry.

## 1. Source Recognition

The original SVG is parsed into recognition IR: drawing primitives, labels, candidate rooms, structures, parking, cores, no-go zones, and mutable zones.

## 2. Intent Layer

Codex or another LLM converts user direction into structured JSON:

- Design intent
- Natural-language edit intent
- Doodle edit intent
- QA repair intent

The LLM does not directly modify SVG geometry.

## 3. OpenCrab MCP Knowledge Layer

OpenCrab MCP supplies the reusable ontology pack, topology graph, claims, evidence, source chunks, program adjacency, and precedent logic.

- Homepage: https://opencrab.sh
- Dashboard: https://opencrab.sh/dashboard
- Desktop: https://opencrab.sh/desktop

The project workflow treats OpenCrab MCP as the source of design intelligence. A design intent is incomplete until it cites the relevant ontology pack and evidence. For community layouts, this means superior-case topology is projected onto the target drawing while preserving protected zones such as parking, columns, cores, ramps, stairs, and egress.

## 4. Deterministic Solver

The solver consumes recognition IR, constraints, standards, ontology evidence, and intent JSON. It generates native SVG patches and rejects changes that violate no-go or lock constraints.

The first executable loop is:

```text
run-job
  -> jobs/job_run_###.json
  -> workflow/export/verify/doctor artifacts

workflow-run
  -> workflow/workflow_run_###.json

revision-run
  -> revisions/revision_run_###.json
  -> repeated natural-language/doodle revision artifacts

Manual loop:
recognize-svg
  -> recognition/recognition_manifest.json
  -> evidence-attach
  -> evidence/evidence_manifest.json
  -> opencrab-sync
  -> opencrab/opencrab_sync_###.json
  -> standards-attach
  -> standards/standards_manifest.json
  -> constraint-attach
  -> constraints/constraint_manifest.json
  -> topology-build
  -> topology/topology_manifest.json
  -> recognition-audit
  -> audits/recognition_audit_###.json
  -> svg-patch-plan
  -> patch_plans/svg_patch_plan_###.json
  -> prompt-edit / sketch-intent
  -> edit-brief
  -> project-status
  -> design-handoff
  -> apply-edit
  -> solver_input.json
  -> engine adapter
  -> alternatives/alternative_###.svg
  -> apply_edit_report.json
  -> review-panel
  -> project-status
  -> export-package
  -> verify-package
  -> doctor
```

`run-job` is the product-facing job contract. A UI, OAuth worker, or MCP wrapper can write one `crab-archi-design-job-spec-v1` JSON file with source SVG, household count, standards, OpenCrab MCP result JSON, doodle constraints, prompt, engine adapter, and verification policy. The command then executes `workflow-run`, `export-package`, `verify-package`, and `doctor`, and records every step in a job report.

`recognize-svg` converts the original SVG into a lightweight recognition manifest: XML parse status, viewBox, primitive counts, primitive bounding boxes, column candidates, wall candidates, room-envelope candidates, raster image detection, text label candidates, and program role hints.

`recognize-svg-v2` converts the original SVG into the development-grade Recognition IR v2. It uses the split parser engines in `crab_archi_design.svg`: safe XML loading, namespace normalization, inherited presentation style, unit/viewBox normalization, affine transform accumulation, primitive shape extraction, path flattening, and geometry metrics. `crab_archi_design.recognition` then assigns stable node ids and role hints. This is the path that should feed future topology and same-layer SVG mutation work.

`topology-build` converts recognition, standards, OpenCrab evidence, and drawing constraints into a target topology manifest. It creates nodes for program labels, room envelopes, wall-bounded space regions, structural columns, wall candidates, standards roles, and constraints, then links them with edges such as label-inside-envelope, label-inside-space-region, column-inside-envelope, standard-applies-to-program, protected-geometry, and OpenCrab adjacency targets. When Recognition IR v2 is active, topology prefers it over the lightweight recognition manifest and carries through v2 labels, path-normalized walls, column candidates, constraint polygons, and wall-ray `space_region` evidence.

`recognition-audit` is the design-generation brake. It checks that the target drawing is understood as an architectural plan before ontology projection mutates SVG geometry: positioned program labels, detected wall and column candidates, confirmed community shell, mutable zone, protected no-go zones, active topology, and label-to-envelope or label-to-space-region topology edges. The audit reads v2 recognition metrics first when available, then falls back to the legacy manifest. If it fails, the workflow should return to recognition and constraint correction instead of asking an engine to draw a new layout.

`svg-patch-plan` is the first same-layer mutation artifact. It does not draw an alternative. It identifies existing source SVG elements inside confirmed mutable zones, records their element index/tag/bbox addressing, separates locked candidates in protected zones, anchors changes to recognized program labels, and sets the mutation strategy to `same_layer_element_patch`. A production engine should edit these existing elements rather than adding a new zoning overlay layer.

## Python Module Boundaries

The Python engine should be split before the production parser and solver are expanded. The current CLI remains the compatibility wrapper, but new implementation work should land behind these package boundaries:

- `crab_archi_design.svg`: safe SVG parsing, transforms, units, path flattening, style inheritance, and world-coordinate geometry.
- `crab_archi_design.recognition`: Recognition IR v2 schema, stable node ids, and role classification.
- `crab_archi_design.intents`: DesignIntent/EditIntent schemas and validation. LLMs produce intent JSON, not final coordinates.
- `crab_archi_design.solver`: deterministic feasible-area, sizing, placement, local-search, and same-layer SVG mutation contracts.
- `crab_archi_design.qa`: hard/soft gate helpers shared by recognition, topology, solver, and export checks.

This split keeps the production path from becoming another overlay engine. SVG parsing creates IR, intent stays declarative, the solver mutates recognized elements, and QA decides whether the result is releasable.

The split is intentionally closer to multiple small engines than one large Python script:

1. Parser engines create stable geometry facts from SVG.
2. Recognition engines attach architectural role hints and confidence.
3. Topology engines connect labels, envelopes, protected elements, standards, and OpenCrab evidence.
4. Intent engines translate natural language and doodles into bounded edit JSON.
5. Solver engines search only inside mutable regions and emit native SVG patches.
6. QA engines reject candidates that violate shell, parking, core, column, ramp, egress, standards, or topology constraints.

`create-job` is the product-facing first-run entry point. It turns uploaded source SVG, standards, OpenCrab MCP evidence, doodle constraints, prompt text, engine policy, and export settings into a `crab-archi-design-job-spec-v1` file that can be validated and executed by workers without hand-written JSON. With `--brief`, it also writes a Markdown review brief summarizing the job, referenced files, validation checks, and next commands.

`workflow-run` orchestrates the same manual commands in a single run. It initializes the project when needed, executes the gates in order, writes all normal artifacts, and records the step-by-step result in a workflow report.

`revision-run` is the product-facing repeat-edit contract for an existing project. It can attach updated constraints, rebuild topology, add natural-language and doodle intents, rebuild the edit brief and design handoff, run the selected engine adapter, generate a review panel, and record the full revision in `revisions/revision_run_###.json`.

`opencrab-sync` is the MCP bridge. It normalizes `opencrab_query`, `opencrab_search_documents`, or similar OpenCrab MCP JSON results into a project sync artifact and appends the extracted evidence to the evidence manifest.

`constraint-attach` converts doodle strokes into enforceable project constraints such as community shell, no-go zones, lock boundaries, mutable zones, and projectable zones.

`standards-attach` converts area/program standards into a project manifest. CSV files are parsed into selected household-count rows; Excel, PDF, and JSON files are attached as verified source references for the engine adapter.

`edit-brief` writes JSON and Markdown review artifacts before SVG mutation. It is intentionally lightweight: it verifies source recognition, topology, OpenCrab evidence, standards and constraint manifests, checks source SVG parsing, summarizes operations, and flags doodle strokes that fall outside the source SVG viewBox.

`project-status` writes a project command-center JSON file. It summarizes recognition, topology, OpenCrab evidence, standards, constraints, edit intents, latest apply reports, latest alternative SVGs, and review panels so an agent can decide whether the project is ready for solver handoff or candidate review.

`design-handoff` writes a Codex/LLM/MCP/engine handoff package. It combines status gates, recognition summaries, topology summaries, OpenCrab evidence, standards excerpts, constraints, operations, prompt blocks, and output contracts so design generation starts from the same evidence-backed project state every time.

`export-package` creates the portable handoff bundle. It writes an export manifest and ZIP containing the latest project status, manifests, OpenCrab sync files, intents, design handoff, workflow report, apply report, engine reports, alternative SVG, and review panel. Source SVG is included only when explicitly requested.

`verify-package` validates exported ZIPs before downstream use. It checks ZIP integrity, embedded manifest presence, archive membership, required artifacts, file sizes, and SHA-256 hashes. This is the recommended boundary before GitHub, SaaS, or another MCP agent consumes the package.

`doctor` is the operational readiness check. It combines local install checks, OpenCrab MCP configuration checks, `project-status` gates, latest candidate SVG checks, and optional `verify-package` results into `diagnostics/doctor_report_###.json`. Use it before CI promotion, OAuth upload, MCP handoff, or GitHub release workflows.

`release-audit` is the final handoff gate. It combines project candidate readiness, OpenCrab evidence verification, topology/standards/constraints readiness, native SVG checks, package verification, and doctor diagnostics into `audits/release_audit_###.json`.

`mcp-manifest` emits the machine-readable CLI tool catalog for Codex exec, MCP wrappers, OAuth upload workers, and SaaS ingestion services. It includes `create_job`, `validate_job`, and `run_job` as the SaaS/OAuth handoff path. `mcp-config` emits runtime config for those clients, and `mcp-smoke` verifies the configured stdio server. `crab-archi-design-mcp --stdio` exposes the same catalog through a dependency-free stdio JSON-RPC bridge. See `docs/mcp_oauth_integration.md`.

The engine adapter may be a Python script, local executable, or MCP-backed wrapper. It receives environment variables such as `CRAB_ARCHI_SOLVER_INPUT`, `CRAB_ARCHI_RUN_DIR`, `CRAB_ARCHI_PROJECT_DIR`, and `CRAB_ARCHI_SOURCE_SVG`.

The built-in `layout-svg-engine` adapter is now treated as a diagnostic room-envelope solver, not a sufficient production design author by itself. It writes an overlay-style redraw layer, which is useful for end-to-end QA but not acceptable as the high-quality architectural output. The production direction is to consume `svg-patch-plan` and mutate recognized source SVG elements in place.

The built-in `reference-svg-engine` adapter remains available for end-to-end diagnostics. It copies the source SVG into a native SVG candidate, adds a compact reference layer with operations/evidence/standards/constraints summary, and writes a quality-gated engine report.

The local `tools/doodle_editor.html` utility turns hand-drawn browser strokes into the same sketch JSON consumed by `sketch-intent`.

## Adapter Roadmap

1. Built-in layout adapter for standards-backed room-envelope redraw.
2. Built-in reference adapter for end-to-end workflow validation.
3. CLI adapter for local project-specific geometry execution.
4. OpenCrab MCP adapter for pack lookup, evidence retrieval, topology projection, and QA citation.
5. MCP adapter for agent-safe drawing operations.
6. Export package adapter for GitHub handoff and SaaS upload.
7. OAuth/SaaS adapter for user-scoped standards, pack access, run history, and exports.
