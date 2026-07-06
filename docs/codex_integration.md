# Codex Integration

Crab Archi Design is agent-runtime-neutral. Codex CLI can drive it three ways;
all three converge on the same tested CLI pipeline and gates.

## 0. Contract

Codex reads [`AGENTS.md`](../AGENTS.md) at the repository root. It contains the
hard rules (intent JSON only, OpenCrab evidence, protected geometry, native
SVG), canonical commands, artifact paths, and the definition of done. Keep that
file authoritative; this document only covers wiring.

## 1. Exec mode (recommended for Codex CLI sessions)

Codex runs the CLI directly. Generate the machine-readable tool catalog so the
agent can discover subcommands, arguments, outputs, and gates:

```bash
crab-archi-design mcp-manifest --output integrations/crab_archi_design_mcp_manifest.json
```

The manifest's `recommended_sequences` are safe orderings, e.g.
`new_project_to_candidate = workflow-run → export-package → verify-package →
doctor → release-audit`. Use `--strict` everywhere in automation: non-pass
states exit non-zero.

## 2. MCP stdio server

For Codex's MCP client (or any MCP client):

```bash
crab-archi-design mcp-config --output integrations/crab_archi_design_mcp_config.json
```

The output embeds a ready-to-paste `codex.mcpServers` block:

```json
{
  "mcpServers": {
    "crab-archi-design": {
      "command": "crab-archi-design-mcp",
      "args": ["--stdio"],
      "env": { "CRAB_ARCHI_PROJECT_ROOT": "projects" }
    }
  }
}
```

Register with `codex mcp add crab-archi-design -- crab-archi-design-mcp --stdio`
and verify the runtime before first use:

```bash
crab-archi-design mcp-smoke --config integrations/crab_archi_design_mcp_config.json --strict
```

Security note: MCP `raw_args` cannot enable custom engine adapters; the
allowlisted deterministic engines are the only executable path from MCP.

## 3. Localhost studio (shared human/agent surface)

```bash
crab-archi-design studio --project-root projects --port 8765 --open
```

A human marks the source SVG, optionally draws one community-shell polygon, and
states the request in natural language. The studio defaults to
`layout-svg-engine --standalone-redraw`, so candidates are clean native SVG
redraws rather than brittle source-line patch attempts. Codex can drive the
identical HTTP API headlessly (`/api/load-svg`, `/api/run`, `/api/artifact`,
`/api/status` — see AGENTS.md for stroke modes). Both produce
`projects/<id>/studio/studio_run_###/studio_run_report.json` plus the standard
workflow artifacts, so a human annotation session and an agent revision loop
compose on the same project without translation.

## Job specs for fire-and-forget runs

For queue/CI-style execution, Codex should write a
`crab-archi-design-job-spec-v1` JSON (see `examples/job_spec_sample.json`,
including `scale_mm_per_world` and `engine_arg`), then:

```bash
crab-archi-design validate-job --job job.json --strict
crab-archi-design run-job --job job.json --strict
```
