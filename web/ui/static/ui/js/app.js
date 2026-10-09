/* GROOVE app.js — wspólne helpery UI (Etap 2 audytu). Bez zależności, bez frameworka.
   window.gvModal.open/close — jeden modal zamiast 5 ręcznych overlayów (P7).
   window.gvToast(msg, {type,timeout}) — jedna kolejka toastów. */
(function () {
  "use strict";

  // ── Modal ──
  function open(el) { if (el) { el.setAttribute("data-open", ""); document.addEventListener("keydown", esc); } }
  function close(el) { if (el) { el.removeAttribute("data-open"); document.removeEventListener("keydown", esc); } }
  function esc(e) { if (e.key === "Escape") document.querySelectorAll(".gv-modal[data-open]").forEach(close); }
  // Klik w tło zamyka; [data-gv-close] wewnątrz też.
  document.addEventListener("click", function (e) {
    var m = e.target.closest(".gv-modal");
    if (m && (e.target === m || e.target.closest("[data-gv-close]"))) close(m);
    var opener = e.target.closest("[data-gv-open]");
    if (opener) { var t = document.querySelector(opener.getAttribute("data-gv-open")); open(t); }
  });
  window.gvModal = { open: open, close: close };

  // ── Toast ──
  var box = null;
  function toasts() { if (!box) { box = document.createElement("div"); box.className = "gv-toasts"; box.setAttribute("aria-live", "polite"); document.body.appendChild(box); } return box; }
  window.gvToast = function (msg, opts) {
    opts = opts || {};
    var t = document.createElement("div");
    t.className = "gv-toast gv-toast--" + (opts.type || "info");
    t.setAttribute("role", "status");
    t.textContent = msg;
    toasts().appendChild(t);
    var ms = opts.timeout == null ? 4000 : opts.timeout;   // 0/false = nie znika sam (błędy krytyczne)
    if (ms) setTimeout(function () { t.remove(); }, ms);
    return t;
  };
})();

/* ── Potwierdzenie akcji destrukcyjnych (etap 3 audytu UX) ──
   data-confirm="Usunąć produkt X?" na <button>/<a>/<form>. Modal z tokenów zamiast
   window.confirm; ogłoszenie przez role=alertdialog. Po „Tak" akcja idzie normalnie
   (strażnik double-submit niżej też działa). Menu „Więcej" (<details class="menu">)
   zamyka się klikiem obok i Esc. */
(function () {
  "use strict";
  var m = null, go = null;
  function modal() {
    if (m) return m;
    m = document.createElement("div");
    m.className = "gv-modal"; m.id = "gv-confirm";
    m.innerHTML = '<div class="gv-modal__box" role="alertdialog" aria-modal="true" aria-labelledby="gv-confirm-t">' +
      '<div class="gv-modal__title" id="gv-confirm-t">Potwierdź</div><div class="gv-modal__text" id="gv-confirm-x"></div>' +
      '<div class="gv-modal__actions"><button type="button" class="btn btn-secondary" data-gv-close>Anuluj</button>' +
      '<button type="button" class="btn btn-danger-solid" id="gv-confirm-ok" data-testid="confirm-ok">Tak, wykonaj</button></div></div>';
    document.body.appendChild(m);
    m.querySelector("#gv-confirm-ok").addEventListener("click", function () {
      window.gvModal.close(m); var f = go; go = null; if (f) f();
    });
    return m;
  }
  function ask(text, action) {
    var el = modal(); el.querySelector("#gv-confirm-x").textContent = text; go = action;
    window.gvModal.open(el); el.querySelector("#gv-confirm-ok").focus();
  }
  window.gvConfirm = ask;
  // Przycisk/link z data-confirm (capture — przed strażnikiem submitu i handlerami widoku).
  document.addEventListener("click", function (e) {
    var t = e.target.closest("[data-confirm]");
    if (!t || t.tagName === "FORM" || t.hasAttribute("data-gv-confirmed")) return;
    e.preventDefault(); e.stopImmediatePropagation();
    ask(t.getAttribute("data-confirm"), function () {
      t.setAttribute("data-gv-confirmed", ""); t.click(); t.removeAttribute("data-gv-confirmed");
    });
  }, true);
  // Formularz z data-confirm (np. submit Enterem).
  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (!f.hasAttribute || !f.hasAttribute("data-confirm") || f.hasAttribute("data-gv-confirmed")) return;
    e.preventDefault(); e.stopImmediatePropagation();
    var sub = e.submitter;
    ask(f.getAttribute("data-confirm"), function () {
      f.setAttribute("data-gv-confirmed", "");
      if (f.requestSubmit) f.requestSubmit(sub || undefined); else f.submit();
      f.removeAttribute("data-gv-confirmed");
    });
  }, true);
  // Menu „Więcej": klik obok / Esc zamyka otwarte.
  document.addEventListener("click", function (e) {
    document.querySelectorAll("details.menu[open]").forEach(function (d) { if (!d.contains(e.target)) d.removeAttribute("open"); });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    document.querySelectorAll("details.menu[open]").forEach(function (d) { d.removeAttribute("open"); var s = d.querySelector("summary"); if (s) s.focus(); });
  });
})();

/* ── Strażnik double-submit (UX audyt 2026-09, top-1) ──
   Każdy form[method=post]: po submit blokuje ponowny submit, wyłącza przyciski
   i pokazuje spinner. Opt-out: data-no-guard. Szanuje preventDefault (fetch/ZARIA). */
(function () {
  "use strict";
  function submitButtons(f) {
    return f.querySelectorAll('button[type="submit"], input[type="submit"], button:not([type])');
  }
  function reset(f) {
    f.removeAttribute("data-gv-submitting");
    submitButtons(f).forEach(function (b) {
      b.disabled = false;
      var s = b.querySelector(".gv-spin"); if (s) s.remove();
    });
  }
  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (!f || !f.matches || !f.matches("form")) return;
    // getAttribute, nie f.method — input o name="method" przesłania właściwości formularza
    if ((f.getAttribute("method") || "").toLowerCase() !== "post") return;
    if (f.hasAttribute("data-no-guard") || e.defaultPrevented) return;
    if (f.hasAttribute("data-gv-submitting")) { e.preventDefault(); return; }
    f.setAttribute("data-gv-submitting", "1");
    // disable dopiero po ticku — inaczej name/value klikniętego przycisku nie wejdzie do POST
    setTimeout(function () {
      submitButtons(f).forEach(function (b) {
        b.disabled = true;
        if (b.tagName === "BUTTON" && !b.querySelector(".gv-spin")) {
          var s = document.createElement("span");
          s.className = "gv-spin"; s.setAttribute("aria-hidden", "true");
          b.prepend(s);
        }
      });
    }, 0);
    // ponytail: auto-reset po 20 s — POST-y zwracające plik (download) nie nawigują,
    // a serwerowa idempotencja to osobna warstwa; guard chroni przed serią kliknięć.
    setTimeout(function () { if (document.contains(f)) reset(f); }, 20000);
  });
  // Powrót z bfcache (wstecz) — odblokuj formularze.
  window.addEventListener("pageshow", function () {
    document.querySelectorAll("form[data-gv-submitting]").forEach(reset);
    var lab = document.querySelector("label[data-gv-uploading]");
    if (lab) { lab.removeAttribute("data-gv-uploading"); lab.style.pointerEvents = ""; lab.style.opacity = "";
      var s = lab.querySelector(".gv-spin"); if (s) s.remove(); }
  });

  // ── Upload z feedbackiem (UX audyt top-2) — onchange="gvUpload(this)" na input[type=file].
  // requestSubmit (nie submit()) → odpala event submit, więc strażnik wyżej też działa.
  window.gvUpload = function (input) {
    if (!input.value) return;
    var f = input.form || input.closest("form");
    if (!f) return;
    var lab = input.closest("label");
    if (lab) {
      lab.setAttribute("data-gv-uploading", "1");
      lab.style.pointerEvents = "none"; lab.style.opacity = ".6";
      var s = document.createElement("span");
      s.className = "gv-spin"; s.setAttribute("aria-hidden", "true");
      lab.prepend(s);
    }
    if (window.gvToast) window.gvToast("Importowanie pliku…", { type: "info", timeout: 0 });
    if (f.requestSubmit) f.requestSubmit(); else f.submit();
  };
})();

/* ── Launcher modułów (Ctrl+K / Cmd+K) — Etap 3 audytu UI ── */
(function () {
  var L = document.getElementById("gv-launcher");
  if (!L) return;
  var input = document.getElementById("gv-launcher-input");
  var list = document.getElementById("gv-launcher-list");
  var items = Array.prototype.slice.call(list.querySelectorAll(".gv-launcher__item"));
  var LS = "gv-recent-modules";

  function recent() { try { return JSON.parse(localStorage.getItem(LS) || "[]"); } catch (e) { return []; } }
  function pushRecent(key) {
    try { var r = recent().filter(function (k) { return k !== key; }); r.unshift(key);
      localStorage.setItem(LS, JSON.stringify(r.slice(0, 6))); } catch (e) {}
  }
  function reorderByRecent() {
    var r = recent(); items.sort(function (a, b) {
      var ia = r.indexOf(a.dataset.key), ib = r.indexOf(b.dataset.key);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
    items.forEach(function (it) { list.appendChild(it); });
  }
  function open() { reorderByRecent(); L.setAttribute("data-open", ""); input.value = ""; filter(""); input.focus(); }
  function close() { L.removeAttribute("data-open"); }
  function visible() { return items.filter(function (it) { return !it.hidden; }); }
  function setActive(el) { items.forEach(function (i) { i.classList.remove("is-active"); }); if (el) { el.classList.add("is-active"); el.scrollIntoView({ block: "nearest" }); } }
  function filter(q) {
    q = (q || "").toLowerCase().trim();
    items.forEach(function (it) { it.hidden = q && it.dataset.name.indexOf(q) < 0 && (it.dataset.key || "").indexOf(q) < 0; });
    setActive(visible()[0]);
  }
  function go(el) { if (el) { pushRecent(el.dataset.key); window.location = el.dataset.url; } }

  document.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) { e.preventDefault(); L.hasAttribute("data-open") ? close() : open(); return; }
    if (!L.hasAttribute("data-open")) return;
    var vis = visible(), cur = list.querySelector(".is-active"), idx = vis.indexOf(cur);
    if (e.key === "Escape") { close(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); setActive(vis[Math.min(idx + 1, vis.length - 1)] || vis[0]); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive(vis[Math.max(idx - 1, 0)] || vis[0]); }
    else if (e.key === "Enter") { e.preventDefault(); go(cur || vis[0]); }
  });
  input.addEventListener("input", function () { filter(input.value); });
  list.addEventListener("click", function (e) { var it = e.target.closest(".gv-launcher__item"); if (it) go(it); });
  L.addEventListener("click", function (e) { if (e.target === L) close(); });
  document.querySelectorAll(".gv-launcher-open").forEach(function (b) { b.addEventListener("click", open); });
})();

/* ── Przewijane tabele osiągalne z klawiatury (etap 5 audytu UX, axe scrollable-region-focusable) ──
   .table-wrap, który faktycznie się przewija (telefon), dostaje tabindex=0 + role=region, żeby
   dało się go przewinąć strzałkami. Nieprzewijane zostają poza kolejnością Tab. */
(function () {
  document.querySelectorAll(".table-wrap:not([tabindex])").forEach(function (el) {
    if (el.scrollWidth <= el.clientWidth) return;   // ponytail: raz po załadowaniu, bez resize
    el.tabIndex = 0;
    el.setAttribute("role", "region");
    if (!el.hasAttribute("aria-label")) el.setAttribute("aria-label", "Tabela — przewiń w poziomie");
  });
})();
