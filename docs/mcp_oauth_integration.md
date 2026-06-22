# MCP and OAuth Integration

Crab Archi Design is designed to be driven by Codex, an MCP wrapper, or an OAuth-backed SaaS worker through local exec calls.

The integration boundary is:

```text
Codex / MCP wrapper / OAuth worker
  -> crab-archi-design mcp-manifest
  -> selected CLI tool calls
  -> project JSON artifacts
  -> export-package ZIP
  -> verify-package / doctor
```

## Tool Manifest

Generate the machine-readable catalog:

```bash
crab-archi-design mcp-manifest \
  --output integrations/crab_archi_design_mcp_manifest.json
```

The manifest records:

- CLI subcommands that should be exposed as MCP tools.
- Required and optional arguments for each tool.
- Expected output artifacts.
- Required gates such as `opencrab_evidence_verified`, `latest_alternative_native_svg`, and `package_verify_pass`.
- Recommended command sequences for first-run design, revision loops, and OpenCrab-first manual workflows.
- Security policy for source SVG packaging, native SVG output, raster overlays, and secrets.

## Recommended MCP Tool Mapping

Expose each manifest `tools[].id` as an MCP tool that shells out to:

```text
crab-archi-design --project-root <project_root> <cli_subcommand> ...
```

Use the first stdout line as the primary artifact path unless the tool prints JSON only.

The most important tools are:

```text
workflow_run
opencrab_sync
prompt_edit
sketch_intent
constraint_attach
edit_brief
design_handoff
apply_edit
review_panel
project_status
export_package
verify_package
doctor
doodle_editor
```

## OAuth Worker Pattern

An OAuth or SaaS worker should not mutate SVG directly.

Use this pattern:

1. Receive user-owned SVG, standards, doodle JSON, and prompt through the product UI.
2. Store them in a sandboxed job directory.
3. Call OpenCrab MCP and save the MCP result JSON.
4. Run `workflow-run` with `--opencrab-result-file`.
5. Run `export-package`.
6. Run `verify-package --strict`.
7. Run `doctor --strict`.
8. Upload only the validated ZIP or selected JSON/SVG artifacts.

By default, `export-package` excludes the original source SVG. Include it only with `--include-source-svg` when the receiving environment is allowed to hold proprietary drawings.

## Codex Exec Pattern

Codex can use the same manifest without a custom server:

```bash
crab-archi-design mcp-manifest
crab-archi-design --project-root projects workflow-run ...
crab-archi-design --project-root projects doctor --project-id <project> --zip <zip> --strict
```

The LLM should produce structured intent and handoff artifacts. Native SVG mutation should remain in deterministic adapters such as `reference-svg-engine` or a production room-envelope solver.

## OpenCrab Requirement

OpenCrab MCP is mandatory before a final candidate is accepted.

The manifest advertises:

```text
opencrab.required = true
opencrab.evidence_gate = opencrab_evidence_verified
```

An MCP or OAuth integration should reject final alternatives unless `doctor` confirms the OpenCrab evidence gate and native SVG candidate gates are passing.
