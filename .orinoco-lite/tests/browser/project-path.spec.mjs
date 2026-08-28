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

test('homepage excludes upstream institutional branding', async ({
  page,
  request,
}) => {
  const fixture = await startStaticServer(
    contract.pagesRoot,
    contract.projectPath,
  );
  try {
    await page.goto(projectURL(fixture.origin));
    await expect(
      page.locator(
        'a[href*="fz-juelich"], a[href*="medizin.hhu"]',
      ),
    ).toHaveCount(0);
    await expect(
      page.locator('img[src*="fzj"], img[src*="hhu"]'),
    ).toHaveCount(0);

    for (const asset of ['img/fzj.svg', 'img/hhu.svg']) {
      const response = await request.get(
        projectURL(fixture.origin, asset),
      );
      expect(response.status()).toBe(404);
    }
  } finally {
    await fixture.close();
  }
});

test('bare review route links to open curation pull requests', async ({ page }) => {
  const fixture = await startStaticServer(
    contract.pagesRoot,
    contract.projectPath,
  );
  try {
    const configuration = JSON.parse(
      await readFile(path.join(contract.pagesRoot, 'review', 'config.json'), 'utf8'),
    );
    const expected = new URL(
      `/${configuration.repository}/pulls`,
      'https://github.com',
    );
    expected.searchParams.set('q', 'is:pr is:open label:curation-review');

    const response = await page.goto(
      projectURL(fixture.origin, 'review/'),
    );
    expect(response?.status()).toBe(200);
    await expect(page.getByRole('link', {
      name: 'View open curation pull requests on GitHub',
    })).toHaveAttribute('href', expected.href);
  } finally {
    await fixture.close();
  }
});

test('structured lists keep filtering, grids, and term metadata', async ({ page }) => {
  const fixture = await startStaticServer(
    contract.pagesRoot,
    contract.projectPath,
  );
  try {
    let response = await page.goto(
      projectURL(fixture.origin, 'publications/'),
    );
    expect(response?.status()).toBe(200);
    await expect(page.locator('#orinoco-search')).toBeVisible();
    await expect(page.locator('[data-orinoco-count]')).toContainText('131 results');
    await page.locator('#orinoco-search').fill('DataLad: distributed system');
    await expect(page.locator('[data-orinoco-count]')).toContainText('1 result');

    response = await page.goto(projectURL(fixture.origin, 'instruments/'));
    expect(response?.status()).toBe(200);
    await expect(page.locator('.orinoco-grid .orinoco-card').first()).toBeVisible();

    response = await page.goto(
      projectURL(fixture.origin, 'persons/yaroslav-halchenko/'),
    );
    expect(response?.status()).toBe(200);
    await expect(page.locator('.orinoco-term-title')).toContainText(
      'Yaroslav Halchenko',
    );
    await expect(page.locator('#sigma-container')).toBeVisible();
  } finally {
    await fixture.close();
  }
});
