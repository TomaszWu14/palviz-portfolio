// Testy wizualne GROOVE (etap 4 audytu UX): toHaveScreenshot kluczowych widoków.
// Uruchom: make test-visual   (albo: cd tests/e2e && npx playwright test)
// Nowe wzorce po świadomej zmianie wyglądu: make test-visual-update.
// Wzorce są robione na Windows (renderowanie fontów zależy od systemu) — suita lokalna, nie CI.
// Testy przepływów (@flow, flow.spec.ts) nie robią zrzutów → działają na każdym OS; nocny CI:
// .github/workflows/e2e-nightly.yml. Uruchom: make test-flow (npx playwright test --grep @flow).
import { defineConfig } from '@playwright/test';

const PORT = 8899;
const py = process.env.PYTHON ?? 'python';

export default defineConfig({
  testDir: '.',
  timeout: 60_000,
  workers: 1,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : [['list']],
  snapshotPathTemplate: '{testDir}/__screenshots__/{projectName}/{arg}{ext}',
  // timeout 15 s: strona design systemu renderuje się ~5–6 s — domyślne 5 s dawało losowe
  // „Failed to take two consecutive stable screenshots” przy obciążonej maszynie.
  expect: { timeout: 15_000, toHaveScreenshot: { maxDiffPixelRatio: 0.01, animations: 'disabled', caret: 'hide' } },
  use: { baseURL: `http://127.0.0.1:${PORT}`, locale: 'pl-PL', timezoneId: 'Europe/Warsaw' },
  projects: [
    { name: 'desktop', grepInvert: /@flow/, use: { viewport: { width: 1366, height: 768 }, colorScheme: 'light' } },
    { name: 'dark', grepInvert: /@flow/, use: { viewport: { width: 1366, height: 768 }, colorScheme: 'dark' } },
    { name: 'phone', grepInvert: /@flow/, use: { viewport: { width: 390, height: 844 }, colorScheme: 'light', isMobile: true, hasTouch: true } },
    // Przepływy zmieniają dane (np. księgują HU) — jeden projekt, jeden przebieg na świeżej bazie.
    { name: 'flow', grep: /@flow/, use: { viewport: { width: 1366, height: 768 }, trace: 'retain-on-failure' } },
  ],
  webServer: {
    // Świeża baza + sesje (prepare.py), dopiero potem serwer — plik bazy nie może być otwarty.
    command: `${py} prepare.py && ${py} ../../web/manage.py runserver 127.0.0.1:${PORT} --noreload`,
    url: `http://127.0.0.1:${PORT}/health/`,
    reuseExistingServer: false,
    stdout: 'ignore',
    timeout: 120_000,
    env: {
      DB_PATH: `${__dirname}/.tmp/e2e.sqlite3`, DJANGO_DEBUG: 'true', DJANGO_ALLOWED_HOSTS: '*',
      DJANGO_SECRET_KEY: 'e2e-visual-0123456789-abcdefghijklmnopqrstuvwxyz', CELERY_BROKER_URL: 'memory://',
      PYTHONPATH: `${__dirname}/../../web${process.platform === 'win32' ? ';' : ':'}${__dirname}/../..`,
    },
  },
});
