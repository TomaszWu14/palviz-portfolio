/* Reużywalny typeahead REF — wzorzec wydzielony z phv/home (UX audyt #9).
 *
 * Podłącz do dowolnego <input> deklaratywnie:
 *   <input name="ref_code" data-ref-ac data-ref-url="{% url 'ui:phv_suggest' %}">
 *
 * Atrybuty:
 *   data-ref-ac      (wymagane) włącza widget
 *   data-ref-url     (wymagane) endpoint zwracający {results:[{code,name}]}
 *   data-ref-submit  (opcja) po wyborze podpowiedzi wyślij najbliższy <form>
 *                    (dla pól-wyszukiwarek; bez tego tylko wypełnia input)
 *
 * Inert bez inputów z data-ref-ac — ładowany globalnie z base.html.
 */
(function () {
  var STYLE = '.ref-ac{position:absolute;top:100%;left:0;right:0;z-index:60;margin-top:4px;'
    + 'background:var(--surface,#fff);border:1px solid var(--gray-300,#cbd5e1);border-radius:10px;'
    + 'box-shadow:0 8px 24px rgba(2,6,23,.18);overflow:hidden;max-height:320px;overflow-y:auto}'
    + '.ref-ac__opt{padding:10px 13px;cursor:pointer;border-bottom:1px solid var(--gray-100,#eef2f6);'
    + 'display:flex;gap:8px;align-items:baseline}'
    + '.ref-ac__opt:last-child{border-bottom:0}'
    + '.ref-ac__opt--on{background:var(--gray-100,#eef2f6)}'
    + '.ref-ac__code{font-family:monospace;font-size:14px;color:var(--blue,#2563eb)}'
    + '.ref-ac__name{font-size:13px;color:var(--gray-500,#5f7a76);overflow:hidden;'
    + 'text-overflow:ellipsis;white-space:nowrap}';

  function injectStyle() {
    if (document.getElementById('ref-ac-style')) return;
    var s = document.createElement('style');
    s.id = 'ref-ac-style';
    s.textContent = STYLE;
    document.head.appendChild(s);
  }

  function attach(input) {
    var url = input.getAttribute('data-ref-url');
    if (!url || input._refAcBound) return;
    input._refAcBound = true;
    input.setAttribute('autocomplete', 'off');
    var form = input.closest('form');
    var doSubmit = input.hasAttribute('data-ref-submit');

    var wrap = input.parentNode;
    if (getComputedStyle(wrap).position === 'static') wrap.style.position = 'relative';
    var box = document.createElement('div');
    box.className = 'ref-ac';
    box.setAttribute('role', 'listbox');
    box.hidden = true;
    wrap.appendChild(box);

    var timer = null, active = -1, items = [];

    function hide() { box.hidden = true; box.innerHTML = ''; active = -1; items = []; }

    function pick(code) {
      input.value = code;
      hide();
      if (doSubmit && form) form.submit(); else input.focus();
    }

    function setActive(i) {
      active = i;
      Array.prototype.forEach.call(box.children, function (c, j) {
        c.classList.toggle('ref-ac__opt--on', j === i);
      });
    }

    function render(results) {
      items = results;
      if (!results.length) { hide(); return; }
      box.innerHTML = '';
      results.forEach(function (r, i) {
        var el = document.createElement('div');
        el.className = 'ref-ac__opt';
        el.setAttribute('role', 'option');
        // textContent (nie innerHTML) — nazwy z importów Excel/SAP; HTML w nazwie
        // wykonywałby się u każdego operatora (stored XSS). Patrz phv/home.
        var s1 = document.createElement('strong');
        s1.className = 'ref-ac__code';
        s1.textContent = r.code;
        var s2 = document.createElement('span');
        s2.className = 'ref-ac__name';
        s2.textContent = r.name || '';
        el.appendChild(s1); el.appendChild(s2);
        el.addEventListener('mousedown', function (e) { e.preventDefault(); pick(r.code); });
        el.addEventListener('mouseenter', function () { setActive(i); });
        box.appendChild(el);
      });
      box.hidden = false;
    }

    function fetchSuggest(q) {
      fetch(url + (url.indexOf('?') > -1 ? '&' : '?') + 'q=' + encodeURIComponent(q),
            { headers: { 'X-Requested-With': 'fetch' } })
        .then(function (r) { return r.json(); })
        .then(function (d) { if (input.value.trim() === q) render(d.results || []); })
        .catch(function () { /* cicho — brak podpowiedzi nie blokuje wpisywania */ });
    }

    input.addEventListener('input', function () {
      var q = input.value.trim();
      clearTimeout(timer);
      if (!q) { hide(); return; }
      timer = setTimeout(function () { fetchSuggest(q); }, 150);
    });

    input.addEventListener('keydown', function (e) {
      if (box.hidden || !items.length) return;
      if (e.key === 'ArrowDown') { e.preventDefault(); setActive((active + 1) % items.length); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((active - 1 + items.length) % items.length); }
      else if (e.key === 'Enter' && active >= 0) { e.preventDefault(); pick(items[active].code); }
      else if (e.key === 'Escape') { hide(); }
    });

    input.addEventListener('blur', function () { setTimeout(hide, 120); });
  }

  function init() {
    var inputs = document.querySelectorAll('input[data-ref-ac]');
    if (!inputs.length) return;
    injectStyle();
    inputs.forEach(attach);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
