import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '../../..');

export default defineConfig({
  expect: { timeout: 15_000 },
  fullyParallel: false,
  outputDir: path.join(ROOT, 'build/playwright/results'),
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'webkit', use: { ...devices['Desktop Safari'] } },
  ],
  reporter: [
    ['list'],
    [
      'html',
      {
        open: 'never',
        outputFolder: path.join(ROOT, 'build/playwright/report'),
      },
    ],
  ],
  retries: 0,
  testDir: HERE,
  testMatch: ['project-path.spec.mjs'],
  timeout: 90_000,
  use: {
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    video: 'off',
  },
  workers: 1,
});
