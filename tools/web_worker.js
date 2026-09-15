import { loadPyodide } from 'https://cdn.jsdelivr.net/pyodide/v0.27.7/full/pyodide.mjs';

const ready = (async () => {
  const runtime = await loadPyodide({ indexURL: 'https://cdn.jsdelivr.net/pyodide/v0.27.7/full/' });
  const response = await fetch('/cad_runtime.zip');
  if (!response.ok) throw new Error('CAD runtime download failed');
  runtime.unpackArchive(await response.arrayBuffer(), 'zip', { extractDir: '/home/pyodide' });
  runtime.runPython('from crab_archi_design.web_runtime import dispatch');
  return runtime;
})();

// Serialize every command: overlapping mutations must retain input order.
let queue = Promise.resolve();
self.onmessage = ({ data }) => {
  queue = queue.then(async () => {
    try {
      const runtime = await ready;
      runtime.globals.set('_request_json', JSON.stringify(data.request));
      const result = JSON.parse(runtime.runPython('dispatch(_request_json)'));
      self.postMessage({ id: data.id, result });
    } catch (error) {
      self.postMessage({ id: data.id, result: { status: 'error', error: String(error) } });
    }
  });
};
