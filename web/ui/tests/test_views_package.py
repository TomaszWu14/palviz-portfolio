"""Integrity tests for the split `ui.views` package — guards the modularization:
every URL still resolves to a callable, and the private helpers that other modules
(tests, tasks) import from `ui.views` remain re-exported."""
from django.test import SimpleTestCase
from django.urls import get_resolver


class ViewsPackageIntegrityTests(SimpleTestCase):
    def test_all_url_callbacks_are_callable(self):
        """Every URL pattern must map to an importable, callable view. This fails loudly
        if the package re-export surface drops a view referenced in urls.py."""
        missing = []

        def walk(patterns, prefix=""):
            for p in patterns:
                if hasattr(p, "url_patterns"):
                    walk(p.url_patterns, prefix + str(p.pattern))
                else:
                    if not callable(getattr(p, "callback", None)):
                        missing.append(prefix + str(p.pattern))

        walk(get_resolver().url_patterns)
        self.assertEqual(missing, [], f"URL(s) without a callable view: {missing}")

    def test_private_helpers_still_importable(self):
        """tasks.py and several tests import these `_`-prefixed helpers from ui.views."""
        from ui import views
        for name in [
            "_build_pallet", "_eval_layouts", "_render_panel", "_recalculate_instruction",
            "_svg_thumbnail", "_form_max_height", "_optimize_packaging", "_suggest_packaging",
            "_best_box", "_build_stack_base", "_compute_cog",
        ]:
            self.assertTrue(hasattr(views, name), f"ui.views lost re-export of {name}")
