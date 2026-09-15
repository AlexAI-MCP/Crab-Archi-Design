import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../tools/canvas_3d.js', import.meta.url), 'utf8');
if (source.includes('THREE.TextureLoader')) throw new Error('3D plan overlay must not use a raster texture');
if (source.includes('/api/render?bbox=')) throw new Error('3D plan overlay must not request a raster render');
if (!source.includes("role: 'native-vector-plan-overlay'")) throw new Error('native SVG plan overlay role is missing');
if (!source.includes('new THREE.LineSegments')) throw new Error('native SVG overlay must use bounded vector line segments');
if (!source.includes('const maximumSegments = 30000')) throw new Error('native plan pattern segment budget changed unexpectedly');
if (!source.includes('depthWrite: false, depthTest: true')) throw new Error('ground plan pattern must be hidden by 3D objects');

const match = source.match(/\/\/ NATIVE_PLAN_OVERLAY_START([\s\S]*?)\/\/ NATIVE_PLAN_OVERLAY_END/);
if (!match) throw new Error('native plan overlay helper section not found');
const helpers = await import(`data:text/javascript,${encodeURIComponent(`${match[1]}\nexport { nativeOverlaySampleIndices };`)}`);

const exact = helpers.nativeOverlaySampleIndices(4, 10);
if (exact.join(',') !== '0,1,2,3') throw new Error(`small overlay sample changed: ${exact}`);
const bounded = helpers.nativeOverlaySampleIndices(174534, 12000);
if (bounded.length !== 12000) throw new Error(`overlay sample is not bounded: ${bounded.length}`);
if (bounded[0] !== 0 || bounded.at(-1) >= 174534) throw new Error('overlay sample indices are outside source bounds');
for (let index = 1; index < bounded.length; index += 1) {
  if (bounded[index] <= bounded[index - 1]) throw new Error('overlay sampling is not deterministic and monotonic');
}
