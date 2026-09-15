# Public Browser Release

## Runtime boundary

The public site runs the existing `CanvasDocument` Python engine in a dedicated
Pyodide Web Worker. Each tab owns its document and undo history. SVG content is
not sent to a server. The last document and request text are stored in this
browser's IndexedDB; downloading SVG is the portable backup. The Python runtime
and Three.js dependencies are downloaded from pinned jsDelivr URLs.

Supported: SVG import (up to 32 MB), native element editing, doodles/zones,
selection transforms, scale calibration, measurement, undo/redo, SVG download,
3D review, camera PNG download, natural-language request recording and JSON
handoff. Embedded scripts, animation, external references, foreignObject, and
style blocks are rejected before replacing a drawing. Export SVG using inline
presentation attributes. Standard local patterns/clip paths remain supported.

The public application does **not** authenticate as Codex or OpenCrab, does not
run autonomous redesign, and does not claim design approval. "Request recorded"
means exactly that. Use the downloaded SVG and `design-request.json` together
in a local Codex/MCP session. DXF/DWG export, private ontology retrieval, and the
long-running design solver remain local features.

## OpenCrab handoff

1. Import the edited SVG as the target drawing and verify its SHA-256 against
   `edited_svg_sha256` in the handoff.
2. Review `messages`, selected element IDs, `viewBox`, scale, and zone `strokes`.
   The browser handoff is a transport format, not a directly executable CLI job.
3. Use OpenCrab MCP to retrieve applicable pack evidence and attach the result
   using `opencrab-sync`. Start with the user's selected project context.
4. Compile the request into the CLI constraint/edit sketch and design intent.
   Do not claim automatic shell recognition from user-drawn zones.
5. Run the local workflow and validate changes to protected geometry, topology,
   program areas, and actual wall linework before accepting an alternative.

OpenCrab: https://opencrab.sh

## Defects repaired

- The local studio no longer assumes the entire drawing is an editable shell.
  New projects require an explicit shell and an actual OpenCrab result file.
  The UI no longer fabricates an operator-confirmed evidence summary, and runs
  now use strict CLI gates.

- Failed compound operations now restore the original geometry, IDs, revision,
  and undo/redo history. Previously the first target could stay edited.
- Scale rejects zero, negative, NaN and infinite values.
- Imported element IDs remain unique even when missing IDs precede existing IDs.
- Protected-zone checks include `protect_zone`, small enclosed zones, edge
  crossings, and the original location of selected mutation targets.
- Public operations cannot request the local `force` override.
- Public SVG parsing rejects active content and remote resource references;
  zone text is rendered as text, not HTML.
- Wheel builds include the studio, doodle editor and canvas assets.
- Messages and camera captures distinguish queued/recorded work from agent execution.

These fixes do not establish automatic architectural recognition accuracy or
construction readiness. Bounding-box protection remains conservative; complex
shells, stair removal, egress, and program-area compliance require the separate
design review workflow. The legacy studio's automatic shell/rectangular redraw
heuristics are not exposed as a public redesign service.

## Build and deploy

```bash
python -m pip install -e '.[test]'
python -m pytest -q
examples/run_quickstart.sh
python tools/build_web.py --vercel
vercel link --project crab-archi-design --yes
vercel deploy --prebuilt --prod
```

`tools/build_web.py` copies only an explicit allowlist into `dist/web` and the
Vercel static output. No proprietary drawing, project directory, account file,
OpenCrab pack content, or local server is deployed. `version.json` identifies
the built Git commit. Commit before making the release build.

Architecture reference: https://pyodide.org/en/stable/usage/index.html
