// Przepływy E2E (TEST-005): asercje na DOM/tekście, BEZ zrzutów → działa na każdym OS.
// Uruchom: make test-flow  (albo: cd tests/e2e && npx playwright test --grep @flow).
// Dane: prepare.py (migrate → seed_testdata, persony p_<slug>, hasło z testkit/factories.py).
// Testy zmieniają bazę (księgują HU) — każdy przebieg startuje na świeżej bazie z prepare.py.
import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const PASSWORD = 'Test-haslo-123';   // testkit/factories.py: PASSWORD
const HU_PLANNED = '003590000000000001';   // seed: HU „planned”, strefa 0011 (kontroler ją ma), 1 pozycja × 100 szt.

async function as(page: Page, persona: string) {
  const sessions = JSON.parse(fs.readFileSync(path.join(__dirname, '.tmp', 'sessions.json'), 'utf8'));
  await page.context().addCookies([{ name: 'sessionid', value: sessions[persona], url: 'http://127.0.0.1:8899' }]);
}

async function login(page: Page, username: string) {
  await page.goto('/login/');
  await page.locator('#id_username').fill(username);
  await page.locator('#id_password').fill(PASSWORD);
  await page.getByRole('button', { name: 'Zaloguj się' }).click();
}

test('logowanie Transport → hub pokazuje tylko moduły tej roli', { tag: '@flow' }, async ({ page }) => {
  await login(page, 'p_transport');
  await expect(page).toHaveURL(/\/$/);
  const tiles = page.locator('.hub__grid .mtile__name');
  await expect(tiles.filter({ hasText: 'Wycena przesyłek' })).toHaveCount(1);
  await expect(tiles.filter({ hasText: 'Paletyzacja' })).toHaveCount(1);
  await expect(tiles.filter({ hasText: 'Data Center' })).toHaveCount(0);
  await expect(tiles.filter({ hasText: 'Kontrola HU' })).toHaveCount(0);
});

test('logowanie kontrolera HU → prosto na skaner', { tag: '@flow' }, async ({ page }) => {
  await login(page, 'p_kontrola_hu');
  await expect(page).toHaveURL(/\/control\//);
  await expect(page.getByTestId('scan-input')).toBeVisible();
});

test('kontrola HU: skan → start → liczenie → zaksięgowanie → status OK', { tag: '@flow' }, async ({ page }) => {
  await as(page, 'Kontrola HU');
  await page.goto('/control/');
  await page.getByTestId('scan-input').fill(HU_PLANNED);
  await page.getByTestId('scan-submit').click();
  await expect(page).toHaveURL(/\/control\/hu\/\d+\/$/);
  await expect(page.getByText(`HU ${HU_PLANNED}`).first()).toBeVisible();

  await page.getByRole('button', { name: 'Rozpocznij kontrolę' }).click();
  await expect(page.getByText(/w kontroli/)).toBeVisible();

  // Liczenie „w ciemno”: kafel SZT; JS skanera sumuje kafle w „Zliczono N szt.”.
  await page.locator('input[name=qty_base]').fill('100');
  await expect(page.getByText('Zliczono 100 szt.')).toBeVisible();
  await page.getByRole('button', { name: 'Potwierdź' }).click();
  await expect(page.getByText('poz. 1/1')).toBeVisible();

  await page.getByRole('button', { name: 'Zaksięguj' }).click();
  await expect(page).toHaveURL(/\/control\/$/);
  await expect(page.getByText('Kontrola zaksięgowana — HU zgodny.')).toBeVisible();

  // Ponowny skan tej samej HU → tylko podgląd (status ok).
  await page.getByTestId('scan-input').fill(HU_PLANNED);
  await page.getByTestId('scan-submit').click();
  await expect(page.getByText('HU zgodny — tylko podgląd')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Zaksięguj' })).toHaveCount(0);
});

test('kalkulator paletyzacji: zmiana parametru → wynik układu', { tag: '@flow' }, async ({ page }) => {
  await as(page, 'Transport');
  await page.goto('/planner/calc/');
  // Karton 40×30×25 na EU 120×80, wys. 215 cm → 8 kart./warstwę × 8 warstw = 64 kart./paletę.
  await page.getByLabel('Sztuk w kartonie').fill('12');
  await page.getByLabel('Sztuk w kartonie').press('Tab');   // hx-trigger „change” → POST /planner/calc/run/
  const result = page.locator('#result-area');
  await expect(result.getByText('Podsumowanie')).toBeVisible();
  await expect(result.locator('tr', { hasText: 'Kart / Paleta' })).toContainText('64');
  await expect(result.getByText('Zapisano wynik')).toBeVisible();
});

test('rola Podgląd → 403 na ekranie planera', { tag: '@flow' }, async ({ page }) => {
  await as(page, 'Podgląd');
  for (const url of ['/planner/calc/', '/planner/shipments/']) {
    const resp = await page.goto(url);
    expect(resp?.status(), url).toBe(403);
    await expect(page).toHaveTitle(/Brak dostępu/);
  }
});
