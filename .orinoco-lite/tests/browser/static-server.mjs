import { createReadStream } from 'node:fs';
import { stat } from 'node:fs/promises';
import { createServer } from 'node:http';
import path from 'node:path';

const CONTENT_TYPES = new Map([
  ['.css', 'text/css; charset=utf-8'],
  ['.html', 'text/html; charset=utf-8'],
  ['.js', 'text/javascript; charset=utf-8'],
  ['.json', 'application/json; charset=utf-8'],
  ['.png', 'image/png'],
  ['.svg', 'image/svg+xml'],
]);

function normalizeMount(value) {
  const leading = value.startsWith('/') ? value : `/${value}`;
  return leading.endsWith('/') ? leading : `${leading}/`;
}

function requestedFile(root, requestPath, mount) {
  if (!requestPath.startsWith(mount)) return null;
  let relative = decodeURIComponent(requestPath.slice(mount.length));
  if (!relative || relative.endsWith('/')) relative += 'index.html';
  const resolvedRoot = path.resolve(root);
  const candidate = path.resolve(resolvedRoot, relative);
  if (
    candidate !== resolvedRoot
    && !candidate.startsWith(`${resolvedRoot}${path.sep}`)
  ) return null;
  return candidate;
}

export async function startStaticServer(root, mount) {
  const normalizedMount = normalizeMount(mount);
  const server = createServer(async (request, response) => {
    const url = new URL(request.url ?? '/', 'http://127.0.0.1');
    const file = requestedFile(root, url.pathname, normalizedMount);
    if (file === null) {
      response.writeHead(404).end('Not found');
      return;
    }
    try {
      const information = await stat(file);
      if (!information.isFile()) throw Object.assign(new Error(), { code: 'ENOENT' });
      response.setHeader(
        'Content-Type',
        CONTENT_TYPES.get(path.extname(file)) ?? 'application/octet-stream',
      );
      response.setHeader('Content-Length', information.size);
      if (request.method === 'HEAD') response.end();
      else createReadStream(file).pipe(response);
    } catch (error) {
      response.writeHead(error?.code === 'ENOENT' ? 404 : 500).end(
        error?.code === 'ENOENT' ? 'Not found' : 'Static fixture failure',
      );
    }
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const address = server.address();
  if (address === null || typeof address === 'string') {
    throw new Error('Could not determine static fixture address');
  }
  return {
    mount: normalizedMount,
    origin: `http://127.0.0.1:${address.port}`,
    async close() {
      const closing = new Promise((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()));
      });
      server.closeAllConnections();
      await closing;
    },
  };
}
