const test = require('node:test');
const assert = require('node:assert');
const { riskyFiles } = require('./risky_paths');

test('ścieżki ryzyka są wykrywane', () => {
  assert.deepStrictEqual(riskyFiles([
    'web/ui/migrations/0200_x.py', 'web/huctl/migrations/0005_y.py', 'web/core/roles.py',
    'web/palletweb/settings.py', 'web/palletweb/config.py', 'Dockerfile', 'docker-entrypoint.sh',
    '.github/workflows/ci.yml',
  ]).length, 8);
});

test('zwykły kod i testy nie blokują auto-merge', () => {
  assert.deepStrictEqual(riskyFiles([
    'web/ui/views/misc.py', 'web/ui/tests/test_roles.py', 'docs/README.md', 'web/palletweb/storage.py',
    'web/core/middleware.py', 'audit/RAPORT.html',
  ]), []);
});
