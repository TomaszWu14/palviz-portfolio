# Archiwum planów i specyfikacji

Historyczne plany implementacji (`superpowers-plans/`) i specyfikacje projektowe
(`superpowers-specs/`) — wszystkie opisują featury **już wdrożone i zmergowane**.
Zostają tu jako zapis historyczny (kontekst decyzji, nie żywa dokumentacja).
Nie edytować treści przeniesionych plików — historyczne samo-referencje
(np. polecenia `gh pr create` w treści planu) są zamrożonym zapisem.

## Weryfikacja wdrożenia (D-02)

| Plik | Feature | Dowód wdrożenia | Werdykt |
|------|---------|------------------|---------|
| `superpowers-plans/2026-08-05-wywolywanie-palet-kontrola.md` + `superpowers-specs/2026-08-05-wywolywanie-palet-kontrola-design.md` | Kolejka wywoływania palet HU (`_call_queue`, `hu_call`, `hu_release`, `hu_escalate`, `EscalationRoute`, `Customer.priority_rank`) | `web/huctl/views/hu_helpers.py:100`, `web/huctl/views/hu_queue.py:14,102,170` — funkcje istnieją (przeniesione do `huctl` w Fazie 2 modularyzacji). Commity `b8d2f83`…`3318c55` ("HU wywołania: …"). Migracje `0112_customer_priority_rank_handlingunit_called_at_and_more.py`, `0113_shipment_picking_complete_and_more.py`, `0117_hustatusevent_kind_escalationroute.py`. | **wdrożony** |
| `superpowers-plans/2026-08-20-carton-opt-ab-redesign.md` + `superpowers-specs/2026-08-20-carton-opt-ab-redesign-design.md` | Model `PackagingRedesign` (Projekt A/B), metryki, widoki, szablony | `web/ui/models/carton_opt.py:159` — klasa `PackagingRedesign` z dokładnie tymi polami (`a_snapshot`, `SCOPE`, `STATUS`). `web/ui/views/carton_opt_redesign.py` + wpisy w `web/ui/urls.py`. Commity `e0292ab`, `a5fc635`, `58edcb7`, `8e32ada`, `9f0f106`, `01cc096`. Migracje `0159_packagingredesign.py`, `0166_redesign_one_per_product.py`. | **wdrożony** (spec dokumentuje, że Faza 4/3D tej pary została zrewertowana — zamierzony kontekst historyczny, treść zachowana) |
| `superpowers-plans/2026-08-24-carton-opt-3d-hierarchy-etap1.md` + `superpowers-specs/2026-08-24-carton-opt-3d-hierarchy-design.md` | `pack_into()`, `_hierarchy_levels()`, pola `CartonAlternative.slot/unit_*/pack_*` | `web/ui/views/carton_opt_variants.py:23,152` — funkcje `pack_into` i `_hierarchy_levels` istnieją. `3b4d60c` merge PR #514, commity `9f48c99`, `a0cc81f`. | **wdrożony** |

Wszystkie 6 plików potwierdzone jako wdrożone (D-02) — żaden nie zostaje w aktywnej
ścieżce z adnotacją "niewdrożony".
