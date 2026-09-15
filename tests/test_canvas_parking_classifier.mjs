import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../tools/canvas_3d.js', import.meta.url), 'utf8');
const match = source.match(/\/\/ PARKING_CLASSIFIER_START([\s\S]*?)\/\/ PARKING_CLASSIFIER_END/);
if (!match) throw new Error('parking classifier section not found');
const classifier = await import(`data:text/javascript,${encodeURIComponent(`${match[1]}\nexport { detectParkingBays };`)}`);
const { detectParkingBays } = classifier;

const lines = [];
const add = (cid, x1, y1, x2, y2) => lines.push({ cid, a: { x: x1, y: y1 }, b: { x: x2, y: y2 } });
for (let index = 0; index < 3; index += 1) {
  const x0 = 100 + index * 20;
  const x1 = x0 + 20;
  add(`p${index}-top`, x0, 100, x1, 100);
  add(`p${index}-bottom`, x0, 140, x1, 140);
  add(`p${index}-left`, x0, 100, x0, 140);
  add(`p${index}-right`, x1, 100, x1, 140);
}
add('p0-diagonal', 100, 100, 120, 140);
add('wall-control', 20, 300, 800, 300);
add('isolated-top', 500, 100, 520, 100);
add('isolated-bottom', 500, 140, 520, 140);
add('isolated-left', 500, 100, 500, 140);
add('isolated-right', 520, 100, 520, 140);

const result = detectParkingBays(lines, { sceneMax: 1000 });
if (result.bays.length !== 3) throw new Error(`expected three repeated bays, got ${result.bays.length}`);
for (const cid of ['p0-top', 'p1-right', 'p2-bottom', 'p0-diagonal']) {
  if (!result.lineCids.has(cid)) throw new Error(`missing parking marking ${cid}`);
}
for (const cid of ['wall-control', 'isolated-top']) {
  if (result.lineCids.has(cid)) throw new Error(`false-positive parking marking ${cid}`);
}
