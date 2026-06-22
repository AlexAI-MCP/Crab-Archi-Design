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
5. Compile design direction into structured intent JSON.
6. Generate native SVG alternatives with a deterministic geometry engine.
7. Let users revise alternatives by natural language or doodle sketch.
8. Run QA gates before exporting the final native SVG.

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

```bash
pip install -e .
```

## CLI

```bash
crab-archi-design init \
  --project-id demo \
  --source-svg /path/to/original.svg \
  --households 900 \
  --standards /path/to/area_standard.xlsx \
  --ontology-pack community_svg_topology_ontology_v2 \
  --opencrab-mcp-server opencrab

crab-archi-design prompt-edit \
  --project-id demo \
  --text "Open the greenery lounge more toward the main hall and keep parking/core locked."

crab-archi-design sketch-intent \
  --project-id demo \
  --sketch examples/sketch_layer_sample.json

crab-archi-design doodle-editor

crab-archi-design apply-edit \
  --project-id demo \
  --intent all \
  --skip-preview

crab-archi-design review-panel \
  --project-id demo

crab-archi-design qa --project-id demo
```

`apply-edit` reads structured natural-language and doodle intents, writes a `solver_input.json`, runs the configured engine adapter, copies the resulting native SVG into `projects/<project>/alternatives/`, and writes an `apply_edit_report.json`.

`doodle-editor` prints the local SVG doodle editor path and `file://` URL. The editor loads a source SVG from your machine, records vector strokes in source viewBox coordinates, and downloads sketch JSON for `sketch-intent`.

`review-panel` generates a local before/after HTML panel with original SVG, alternative SVG, intent summary, apply checks, and engine QA gates.

For the full revision loop, see [docs/edit_loop.md](docs/edit_loop.md).

## Repository Scope

This repository contains the reusable framework shell, schemas, and orchestration layer. It does not include proprietary SVG drawings, project outputs, or OpenCrab-saas internal artifacts.

## Core Principle

```text
Original SVG
  -> Recognition IR
  -> Constraint Graph
  -> OpenCrab MCP Ontology Evidence
  -> Standards Evidence
  -> Design Intent JSON
  -> Native SVG Solver
  -> QA
  -> Natural Language / Doodle Edit Loop
```

## Status

This is the first clean framework extraction. The deterministic recognition/redraw engine is expected to be connected through adapters or MCP tools.
