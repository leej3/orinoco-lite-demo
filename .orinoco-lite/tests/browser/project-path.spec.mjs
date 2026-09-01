import { expect, test } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { startStaticServer } from './static-server.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '../../..');
const pagesRoot = path.resolve(
  ROOT,
  process.env.ORINOCO_PAGES_ROOT ?? 'build/pages',
);
const projectPath = process.env.ORINOCO_PROJECT_PATH ?? '/orinoco-site/';

let fixture;

test.beforeAll(async () => {
  fixture = await startStaticServer(pagesRoot, projectPath);
});

test.afterAll(async () => {
  await fixture.close();
});

test('project-path root is a navigable static page', async ({ page }) => {
  const response = await page.goto(new URL(fixture.mount, fixture.origin).href);
  expect(response?.status()).toBe(200);
  await expect(page.locator('html')).toBeVisible();
  await expect(page.locator('body')).not.toBeEmpty();
  expect(new URL(page.url()).pathname.startsWith(fixture.mount)).toBeTruthy();
});

test('root-relative assets remain under the project path', async ({ page }) => {
  await page.goto(new URL(fixture.mount, fixture.origin).href);
  const localResources = await page.locator('[href], [src]').evaluateAll(
    (elements, origin) => elements
      .map((element) => element.getAttribute('href') ?? element.getAttribute('src'))
      .filter((value) => value !== null)
      .map((value) => new URL(value, window.location.href))
      .filter((url) => url.origin === origin)
      .map((url) => url.pathname),
    fixture.origin,
  );
  for (const resource of localResources) {
    expect(resource.startsWith(fixture.mount), resource).toBeTruthy();
  }
});

test('bare review route links to open curation pull requests', async ({ page }) => {
  const configuration = JSON.parse(
    await readFile(path.join(pagesRoot, 'review', 'config.json'), 'utf8'),
  );
  const expected = new URL(
    `/${configuration.repository}/pulls`,
    'https://github.com',
  );
  expected.searchParams.set('q', 'is:pr is:open label:curation-review');

  const response = await page.goto(
    new URL(`${fixture.mount}review/`, fixture.origin).href,
  );
  expect(response?.status()).toBe(200);
  await expect(page.getByRole('link', {
    name: 'View open curation pull requests on GitHub',
  })).toHaveAttribute('href', expected.href);
});

test('upstream taxonomy presentation keeps filters and list variants', async ({ page }) => {
  let response = await page.goto(
    new URL(`${fixture.mount}publications/`, fixture.origin).href,
  );
  expect(response?.status()).toBe(200);
  await expect(page.locator('.list-layout')).toBeVisible();
  await expect(page.locator('#search')).toBeVisible();
  await expect(page.locator('#publications-count')).toHaveText(/^\d+$/);

  response = await page.goto(
    new URL(`${fixture.mount}projects/`, fixture.origin).href,
  );
  expect(response?.status()).toBe(200);
  await expect(page.locator('.items-grid')).toBeVisible();
});

test('site identity and record editing stay downstream-owned', async ({ page }) => {
  let response = await page.goto(new URL(fixture.mount, fixture.origin).href);
  expect(response?.status()).toBe(200);
  await expect(page.locator('body')).not.toContainText('Psychoinformatics');
  await expect(page.locator('body')).not.toContainText('knowledge pool');
  await expect(page.locator('a[href*="psychoinformatics.de"]')).toHaveCount(0);

  response = await page.goto(
    new URL(`${fixture.mount}projects/`, fixture.origin).href,
  );
  expect(response?.status()).toBe(200);
  const recordHref = await page.locator('.items-grid a[href]').first()
    .getAttribute('href');
  expect(recordHref).not.toBeNull();
  response = await page.goto(new URL(recordHref, page.url()).href);
  expect(response?.status()).toBe(200);
  const editor = page.getByRole('link', { name: 'Edit this record' });
  await expect(editor).toHaveCount(1);
  const href = new URL(await editor.getAttribute('href'), page.url());
  expect(href.origin).toBe(fixture.origin);
  expect(href.pathname).toBe(`${fixture.mount}edit/`);
  expect(href.searchParams.get('sh:NodeShape')).toBe('dlthings:Thing');
  expect(href.searchParams.get('pid')).toBeTruthy();
  expect(href.searchParams.get('edit')).toBe('true');
});
