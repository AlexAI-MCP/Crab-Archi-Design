import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../tools/canvas_3d.js', import.meta.url), 'utf8');
const match = source.match(/\/\/ HEIGHT_CONTROL_START([\s\S]*?)\/\/ HEIGHT_CONTROL_END/);
if (!match) throw new Error('height-control section not found');
const controls = await import(`data:text/javascript,${encodeURIComponent(`${match[1]}\nexport { normalizeReviewHeightMm, floorHeightTargetMm, heightScaleFactor, floorGuideLevels, perspectiveFitDistance };`)}`);
const {
  normalizeReviewHeightMm,
  floorHeightTargetMm,
  heightScaleFactor,
  floorGuideLevels,
  perspectiveFitDistance,
} = controls;

const expect = (actual, expected, label) => {
  if (actual !== expected) throw new Error(`${label}: expected ${expected}, got ${actual}`);
};

expect(normalizeReviewHeightMm(10800), 10800, 'direct building height');
expect(normalizeReviewHeightMm(-5, 3200), 3200, 'invalid direct height uses fallback');
expect(normalizeReviewHeightMm(250000), 200000, 'direct height is capped');

const floors = floorHeightTargetMm(12, 3200);
expect(floors.floors, 12, 'floor count');
expect(floors.floorHeightMm, 3200, 'floor height');
expect(floors.heightMm, 38400, 'floor target height');
expect(floorHeightTargetMm(0, 100).heightMm, 300, 'floor inputs are bounded');

expect(heightScaleFactor(2700, 10800), 4, 'four-floor scale');
expect(heightScaleFactor(3600, 1800), 0.5, 'independent object scale');
expect(floorGuideLevels(12, 40).length, 12, 'small building shows every floor guide');
expect(floorGuideLevels(200, 40).length <= 40, true, 'very tall building keeps floor guides bounded');
expect(floorGuideLevels(200, 40).at(-1), 199, 'bounded guides retain the top floor');
expect(
  perspectiveFitDistance(10, 100, 10) > perspectiveFitDistance(10, 10, 10),
  true,
  'camera fit expands when selected height grows',
);
expect(
  perspectiveFitDistance(10, 100, 10) > 100,
  true,
  'camera fit keeps a tall selected object inside the viewport',
);

const bridgeMatch = source.match(/\/\/ SELECTION_BRIDGE_START([\s\S]*?)\/\/ SELECTION_BRIDGE_END/);
if (!bridgeMatch) throw new Error('selection bridge section not found');
const bridge = await import(`data:text/javascript,${encodeURIComponent(`${bridgeMatch[1]}\nexport { firstMeshForSelectedCids };`)}`);
const fakeMeshes = [
  { userData: { cid: 'building-a', objectKey: 'cid:building-a' } },
  { userData: { cid: 'tree-b', objectKey: 'cid:tree-b' } },
];
expect(bridge.firstMeshForSelectedCids(['building-a'], fakeMeshes), fakeMeshes[0], '2D building selection reaches 3D mesh');
expect(bridge.firstMeshForSelectedCids(['missing', 'tree-b'], fakeMeshes), fakeMeshes[1], 'first available selected CID is used');
expect(bridge.firstMeshForSelectedCids([], fakeMeshes), null, 'empty 2D selection clears 3D selection');
