import { readFile } from 'node:fs/promises';
import path from 'node:path';

import { expect, test } from '@playwright/test';

import { contract, projectURL, ROOT } from './consumer-contract.mjs';
import { startStaticServer } from './static-server.mjs';

const COMMITTED_GRAPH = path.join(
  ROOT,
  'generated/projection/static/graph.json',
);

function edgePairs(graph) {
  return graph.edges.map((edge) => `${edge.source}\0${edge.target}`).sort();
}

function graphResponse(page) {
  return page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === `${contract.projectPath}${contract.graph.path}`;
  });
}

test('project-path graph resources and routes resolve', async ({ page }) => {
  const fixture = await startStaticServer(
    contract.pagesRoot,
    contract.projectPath,
  );
  try {
    const expected = JSON.parse(await readFile(COMMITTED_GRAPH, 'utf8'));
    const pendingGraph = graphResponse(page);
    await page.goto(projectURL(fixture.origin));
    const response = await pendingGraph;
    const responseURL = new URL(response.url());
    expect(responseURL.pathname).toBe(
      `${contract.projectPath}${contract.graph.path}`,
    );
    expect(responseURL.searchParams.get('v')).toMatch(/^[0-9a-f]{64}$/);

    const observed = await response.json();
    expect(new Set(observed.nodes.map((node) => node.id))).toEqual(
      new Set(expected.nodes.map((node) => node.id)),
    );
    expect(edgePairs(observed)).toEqual(edgePairs(expected));
    for (const pid of contract.graph.representative_pids) {
      expect(observed.nodes.some((node) => node.id === pid)).toBe(true);
    }

    const personNode = observed.nodes.find(
      (node) => node.id === contract.test_record.pid,
    );
    expect(personNode).toBeDefined();
    expect(personNode.url).toBe(
      `${contract.projectPath}persons/yaroslav-halchenko`,
    );
    const personPath = `${personNode.url}/`;
    await page.goto(new URL(personPath, fixture.origin).href);
    await expect(page).toHaveURL(
      new RegExp(`${contract.projectPath}persons/yaroslav-halchenko/$`),
    );
    await expect(
      page.getByRole('heading', { name: contract.test_record.page_heading }),
    ).toBeVisible();
    expect(
      fixture.requests.every(({ method }) => ['GET', 'HEAD'].includes(method)),
    ).toBe(true);
  } finally {
    await fixture.close();
  }
});
