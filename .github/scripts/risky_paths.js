// Ścieżki wysokiego ryzyka: PR, który je zmienia, nie scala się automatycznie (audyt CICD-002).
// Migracje, kontrakt ról, konfiguracja, obraz i CI trafiają na prod bez recenzji — tu wymagamy
// ręcznego przeglądu i kliknięcia „Merge”.
const RISKY = [
  /(^|\/)migrations\//,
  /^web\/core\/roles\.py$/,
  /^web\/palletweb\/(settings|config)\.py$/,
  /^Dockerfile$/,
  /^docker-entrypoint\.sh$/,
  /^\.github\//,
];

function riskyFiles(paths) {
  return paths.filter((p) => RISKY.some((re) => re.test(p)));
}

module.exports = { riskyFiles };
