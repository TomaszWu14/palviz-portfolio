// Regresja wizualna kluczowych widoków (design system etapu 3). Wzorce: __screenshots__/<projekt>/.
import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const sessions = (): Record<string, string> =>
  JSON.parse(fs.readFileSync(path.join(__dirname, '.tmp', 'sessions.json'), 'utf8'));

// Treść zależna od czasu/sieci — maskowana, żeby zrzut nie „pływał”.
const volatile = (page: Page) => [
  page.locator('.toolbar__count'),                                   // kurs NBP (sieć)
  page.getByText(/po terminie|temu|\d+\s?h\s?\d+\s?min|\d+\s?min\b|dziś|wczoraj/i),  // czasy względne / SLA
  page.locator('time, [data-volatile]'),
];

async function shot(page: Page, persona: string | null, url: string, name: string) {
  if (persona) {
    await page.context().addCookies([{ name: 'sessionid', value: sessions()[persona], url: 'http://127.0.0.1:8899' }]);
  }
  const resp = await page.goto(url, { waitUntil: 'networkidle' });
  expect(resp?.status(), `${url} dla ${persona ?? 'anon'}`).toBeLessThan(400);
  await page.evaluate(() => document.fonts.ready);
  await expect(page).toHaveScreenshot(`${name}.png`, { fullPage: true, mask: volatile(page) });
}

const VIEWS: [string, string | null, string][] = [
  ['logowanie', null, '/login/'],
  ['hub', 'superuser', '/'],
  ['design-system', 'superuser', '/ui/'],
  ['produkty', 'superuser', '/planner/products/'],
  ['kartony', 'superuser', '/planner/cartons/'],
  ['klienci', 'superuser', '/planner/customers/'],
  ['wysylki', 'superuser', '/planner/shipments/'],
  ['magazyn', 'Transport', '/magazyn/'],
  ['skaner-menu', 'Kontrola HU', '/control/'],
  ['skaner-zmiana', 'Kontrola HU', '/control/my/'],
];

for (const [name, persona, url] of VIEWS) {
  test(name, async ({ page }) => shot(page, persona, url, name));
}

test('wysylka-szczegoly', async ({ page }) => {
  await page.context().addCookies([{ name: 'sessionid', value: sessions().superuser, url: 'http://127.0.0.1:8899' }]);
  await page.goto('/planner/shipments/');
  const href = await page.locator('table.table tbody a.font-semibold').first().getAttribute('href');
  await shot(page, 'superuser', href!, 'wysylka-szczegoly');
});
