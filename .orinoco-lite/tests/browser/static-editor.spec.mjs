import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { promisify } from 'node:util';

import { expect, test } from '@playwright/test';

import {
  contract,
  editorApplyCommand,
  projectURL,
  reviewBundleFilename,
  ROOT,
} from './consumer-contract.mjs';
import { startStaticServer } from './static-server.mjs';

const execFileAsync = promisify(execFile);

function sha256(value) {
  return createHash('sha256').update(value).digest('hex');
}

function escapedRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

async function assertCatalog(catalog) {
  expect(catalog).toMatchObject({
    format: contract.catalog.format,
    version: contract.catalog.version,
  });
  expect(catalog.source_commit).toMatch(/^[0-9a-f]{40}$/);
  expect(catalog.records.length).toBeGreaterThan(0);
  expect(new Set(catalog.records.map(({ pid }) => pid)).size).toBe(
    catalog.records.length,
  );
  for (const record of catalog.records) {
    expect(Object.keys(record).sort()).toEqual(contract.catalog.record_fields);
    expect(record.path).toMatch(/^site-specific\/metadata\/records\/.+\.ya?ml$/);
    const content = await readFile(path.join(ROOT, record.path));
    expect(record.sha256).toBe(sha256(content));
    expect(record.rdf_turtle).toEqual(expect.any(String));
    expect(record.rdf_turtle.length).toBeGreaterThan(0);
  }
}

test('project-path editor changes a record and downloads without a backend write', async ({
  context,
  page,
}) => {
  await context.addInitScript(() => {
    sessionStorage.setItem('serviceToken', 'inherited-static-token');
  });
  const fixture = await startStaticServer(
    contract.pagesRoot,
    contract.projectPath,
  );
  const mutationRequests = [];
  context.on('request', (request) => {
    if (!['GET', 'HEAD'].includes(request.method())) mutationRequests.push(request);
  });
  try {
    const catalogResponse = await context.request.get(
      projectURL(fixture.origin, contract.catalog.path),
    );
    expect(catalogResponse.ok()).toBe(true);
    const catalog = await catalogResponse.json();
    await assertCatalog(catalog);

    await page.goto(
      projectURL(fixture.origin, 'persons/yaroslav-halchenko/'),
    );
    const publishedEdit = new URL(
      await page.getByRole('link', { name: 'Edit this record' }).getAttribute('href'),
      fixture.origin,
    );
    const editorURL = new URL(
      projectURL(fixture.origin, contract.editor.route),
    );
    editorURL.search = publishedEdit.search;
    editorURL.searchParams.set('token', 'query-static-token');
    await page.goto(editorURL.href);

    await expect(page.getByText('Person', { exact: true }).first()).toBeVisible();
    expect(new URL(page.url()).searchParams.has('token')).toBe(false);
    const givenNameRow = page.locator('.main-row').filter({
      has: page.locator('.row-label', { hasText: /Given name/i }),
    });
    const givenName = givenNameRow.locator('input').first();
    await expect(givenName).toHaveValue('Yaroslav');
    await givenName.fill(contract.test_record.edited_given_name);
    await page.getByRole('button', { name: 'Save', exact: true }).click();

    await page.locator('button:has(.mdi-download)').first().click();
    const submission = page.locator('#submitcomp');
    await expect(
      submission.getByRole('checkbox', {
        name: new RegExp(escapedRegExp(contract.test_record.pid)),
      }),
    ).toBeChecked();
    const downloadPromise = page.waitForEvent('download');
    await submission
      .getByRole('button', { name: 'Download review bundle', exact: true })
      .click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe(
      reviewBundleFilename(contract.test_record.pid),
    );
    const downloaded = await download.path();
    const bundle = JSON.parse(await readFile(downloaded, 'utf8'));
    expect(bundle).toMatchObject({
      format: contract.review_bundle.format,
      source_commit: catalog.source_commit,
      version: contract.review_bundle.version,
    });
    expect(bundle.records).toHaveLength(1);
    expect(Object.keys(bundle.records[0]).sort()).toEqual(
      contract.review_bundle.record_fields,
    );
    expect(bundle.records[0]).toMatchObject({
      pid: contract.test_record.pid,
      schema_type: 'xyzri:XYZPerson',
      source_path: contract.test_record.source_path,
    });
    expect(bundle.records[0].source_sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(bundle.records[0].rdf_turtle).toContain(
      contract.test_record.edited_given_name,
    );

    const command = editorApplyCommand(downloaded);
    const dryRun = await execFileAsync(command.program, command.arguments, {
      cwd: ROOT,
      maxBuffer: 10 * 1024 * 1024,
    });
    const report = JSON.parse(dryRun.stdout);
    expect(report).toEqual({
      applied: false,
      changed_paths: [contract.test_record.source_path],
      diff: expect.any(String),
      format: contract.editor.apply_report.format,
      validated_records: 1,
      version: contract.editor.apply_report.version,
    });
    expect(report.diff).toContain(`b/${contract.test_record.source_path}`);
    expect(report.diff).toContain(contract.test_record.edited_given_name);

    expect(mutationRequests).toEqual([]);
    expect(
      fixture.requests.every(({ method }) => ['GET', 'HEAD'].includes(method)),
    ).toBe(true);
    expect(
      await page.evaluate(() => ({
        local: Object.keys(localStorage),
        session: Object.keys(sessionStorage),
      })),
    ).toEqual({ local: [], session: [] });
  } finally {
    await fixture.close();
  }
});
