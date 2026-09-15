import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../tools/canvas_3d.js', import.meta.url), 'utf8');
const match = source.match(/\/\/ MULTIDISCIPLINE_CLASSIFIER_START([\s\S]*?)\/\/ MULTIDISCIPLINE_CLASSIFIER_END/);
if (!match) throw new Error('multi-discipline classifier section not found');
const classifier = await import(`data:text/javascript,${encodeURIComponent(`${match[1]}\nexport { classifyMultiDiscipline, allowInferredFurniture, classifySiteStructureGeometry, classifyDisciplineRepresentation, representationCapabilities, representationLabel, buildingPresentation, vegetationPresentation, representationColor };`)}`);
const {
  classifyMultiDiscipline,
  allowInferredFurniture,
  classifySiteStructureGeometry,
  classifyDisciplineRepresentation,
  representationCapabilities,
  representationLabel,
  buildingPresentation,
  vegetationPresentation,
  representationColor,
} = classifier;

const expect = (actual, expected, label) => {
  if (actual !== expected) throw new Error(`${label}: expected ${expected}, got ${actual}`);
};

expect(classifyMultiDiscipline({ discipline: 'landscape', role: 'landscape tree_deciduous' }), 'landscape', 'explicit landscape');
expect(classifyMultiDiscipline({ discipline: 'landscape', role: 'civil manhole' }), 'landscape', 'discipline wins conflicting role');
expect(classifyMultiDiscipline({ discipline: 'civil', symbol: 'manhole' }), 'civil', 'explicit civil');
expect(classifyMultiDiscipline({ role: 'exterior facade' }), 'building', 'building mass');
expect(classifyMultiDiscipline({ role: 'site retaining structure' }), 'building', 'explicit structure outranks generic site');
expect(classifyMultiDiscipline({ role: 'contour elevation' }), 'terrain', 'terrain');
expect(classifyMultiDiscipline({ role: 'civil contour' }), 'civil', 'civil contour');
for (const green of ['#4d9854', '#c1dd6e', '#a1b85c', '#dfff7f', '#8cd99f']) {
  expect(classifyMultiDiscipline({ stroke: green, repeated: 3, closed: false, tag: 'circle' }), 'landscape', `green repeated landscape ${green}`);
}
expect(classifyMultiDiscipline({ stroke: '#222222', repeated: 3, closed: true, tag: 'rect' }), null, 'untagged compact indoor furniture fallback');
expect(classifyMultiDiscipline({ stroke: '#222222', repeated: 3, tag: 'polyline', siteDominant: true }), null, 'site-dominant neutral outline is not landscape');
expect(classifyMultiDiscipline({ stroke: '#222222', repeated: 3, tag: 'rect', siteDominant: true }), null, 'site-dominant neutral rectangle remains unassigned');
expect(classifyMultiDiscipline({ stroke: '#222222', siteDominant: true, siteBuilding: true }), 'building', 'inferred site building');
expect(classifyMultiDiscipline({ stroke: '#222222', siteDominant: true, siteStructure: true }), 'building', 'inferred site structure');
for (const role of ['wall', 'column', 'furniture', 'parking']) {
  expect(classifyMultiDiscipline({ role }), null, `legacy ${role}`);
}
expect(allowInferredFurniture({ siteDominant: false, siteStructureContext: false }), true, 'indoor furniture fallback remains');
expect(allowInferredFurniture({ siteDominant: true, siteStructureContext: false }), false, 'site drawing does not invent furniture');
expect(allowInferredFurniture({ siteDominant: false, siteStructureContext: true }), false, 'building detail is not furniture');

const siteGeometryBase = {
  siteDominant: true, green: false, neutralStroke: true, strokeWidth: 0.96,
  strokeThreshold: 0.72, sceneMax: 680,
};
expect(classifySiteStructureGeometry({
  ...siteGeometryBase, closed: true, size: 128, shortSide: 116, aspect: 1.11, pointCount: 59,
}), 'building', 'large heavy closed envelope');
expect(classifySiteStructureGeometry({
  ...siteGeometryBase, closed: false, size: 90, shortSide: 2, aspect: 45, pointCount: 4,
}), 'structure', 'heavy long structural linework');
expect(classifySiteStructureGeometry({
  ...siteGeometryBase, closed: true, size: 20, shortSide: 18, aspect: 1.1, pointCount: 8,
}), null, 'compact repeated symbol is not a building');
expect(classifySiteStructureGeometry({
  ...siteGeometryBase, green: true, closed: true, size: 128, shortSide: 116, aspect: 1.11, pointCount: 59,
}), null, 'green envelope remains landscape evidence');

expect(classifyDisciplineRepresentation({
  channel: 'building', role: 'exterior building', closed: true,
}).subtype, 'building-mass', 'building footprint becomes an editable mass');
expect(classifyDisciplineRepresentation({
  channel: 'building', role: 'retaining structure', closed: false,
}).subtype, 'structure-linear', 'retaining structure stays distinct from a building mass');
expect(classifyDisciplineRepresentation({
  channel: 'landscape', role: 'landscape tree deciduous', tag: 'circle', repeated: 8,
}).subtype, 'tree', 'explicit tree representation');
expect(classifyDisciplineRepresentation({
  channel: 'landscape', tag: 'ellipse', repeated: 12, sizeRatio: 0.012, aspect: 1.2, closed: true,
}).subtype, 'tree', 'compact repeated green ellipse becomes inferred tree');
expect(classifyDisciplineRepresentation({
  channel: 'landscape', tag: 'path', repeated: 0, sizeRatio: 0.006, aspect: 1.4, closed: true,
}).subtype, 'shrub', 'compact round untagged green path becomes a conservative shrub volume');
expect(classifyDisciplineRepresentation({
  channel: 'landscape', tag: 'path', repeated: 0, sizeRatio: 0.006, aspect: 18, closed: true,
}).subtype, 'planting', 'slender green path stays a planting surface');
expect(classifyDisciplineRepresentation({
  channel: 'landscape', role: 'lawn planting', closed: true,
}).subtype, 'planting', 'lawn remains a planting surface');
expect(classifyDisciplineRepresentation({
  channel: 'landscape', role: 'water bioswale', closed: true,
}).subtype, 'water', 'water gets a distinct representation');
expect(classifyDisciplineRepresentation({
  channel: 'civil', role: 'civil road', closed: true,
}).subtype, 'road', 'road gets a distinct civil representation');
expect(classifyDisciplineRepresentation({
  channel: 'terrain', role: 'contour elevation', closed: false,
}).subtype, 'terrain-contour', 'terrain contour remains a line');

expect(representationCapabilities('building-mass').floorEditable, true, 'building mass accepts floor count');
expect(representationCapabilities('building-mass').heightEditable, true, 'building mass accepts height');
expect(representationCapabilities('tree').heightEditable, true, 'tree accepts direct height');
expect(representationCapabilities('tree').floorEditable, false, 'tree never accepts floor count');
expect(representationCapabilities('planting').heightEditable, false, 'planting surface is not extruded by height editor');
expect(representationCapabilities('terrain-surface').heightEditable, false, 'terrain requires elevation evidence');
expect(representationLabel('tree'), '수목', 'localized tree label');

const inferredBuilding = buildingPresentation({ longSideMeters: 19.5 });
expect(inferredBuilding.floors, 6, 'site building footprint gets visible inferred storeys');
expect(inferredBuilding.heightMm, 18000, 'site building storeys produce a visible mass height');
expect(inferredBuilding.heightSource, 'footprint-inferred', 'inferred building height stays labelled');
const explicitBuilding = buildingPresentation({ explicitFloors: 12, floorHeightMm: 3200 });
expect(explicitBuilding.heightMm, 38400, 'explicit floor count controls building height');
expect(explicitBuilding.heightSource, 'svg-floors', 'explicit floor evidence is retained');

const inferredTree = vegetationPresentation({ subtype: 'tree', radiusMeters: 0.12 });
expect(inferredTree.heightMeters, 4.5, 'small plan tree remains visible in site view');
expect(inferredTree.crownRadius >= 0.75, true, 'tree crown is legible at full-site scale');
expect(inferredTree.heightSource, 'geometry-inferred', 'tree visual height stays labelled as inferred');
expect(representationColor('building-mass', 'e100'), representationColor('building-mass', 'e100'),
  'representation palette is deterministic');
