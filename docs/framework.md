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
evidence-attach
  -> evidence/evidence_manifest.json
  -> prompt-edit / sketch-intent
  -> edit-brief
  -> apply-edit
  -> solver_input.json
  -> engine adapter
  -> alternatives/alternative_###.svg
  -> apply_edit_report.json
  -> review-panel
```

`edit-brief` writes JSON and Markdown review artifacts before SVG mutation. It is intentionally lightweight: it verifies OpenCrab evidence, checks source SVG parsing, summarizes operations, and flags doodle strokes that fall outside the source SVG viewBox.

The engine adapter may be a Python script, local executable, or MCP-backed wrapper. It receives environment variables such as `CRAB_ARCHI_SOLVER_INPUT`, `CRAB_ARCHI_RUN_DIR`, `CRAB_ARCHI_PROJECT_DIR`, and `CRAB_ARCHI_SOURCE_SVG`.

The local `tools/doodle_editor.html` utility turns hand-drawn browser strokes into the same sketch JSON consumed by `sketch-intent`.

## Adapter Roadmap

1. CLI adapter for local execution.
2. OpenCrab MCP adapter for pack lookup, evidence retrieval, topology projection, and QA citation.
3. MCP adapter for agent-safe drawing operations.
4. OAuth/SaaS adapter for user-scoped standards, pack access, run history, and exports.
