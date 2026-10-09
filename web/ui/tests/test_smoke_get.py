"""Smoke test: every no-arg GET view should not 500 (logged-in superuser, empty DB)."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import get_resolver


def _argfree_named_urls():
    resolver = get_resolver()
    out = []
    for key, (patterns, _regex) in resolver.reverse_dict.items():
        if not isinstance(key, str):
            continue
        # take the first pattern variant; keep only those with zero args
        for pat, params in patterns:
            if not params:           # no required arguments
                out.append((key, "/" + pat.rstrip("/") + "/" if pat else "/"))
                break
    return out


class SmokeGetTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(username="b", password="x")
        self.client.post("/login/", {"username": "b", "password": "x"})

    def test_all_argfree_get_views(self):
        from django.urls import reverse
        failures = []
        seen = set()
        resolver = get_resolver()
        for key in list(resolver.reverse_dict.keys()):
            if not isinstance(key, str) or key in seen:
                continue
            seen.add(key)
            try:
                url = reverse(key)
            except Exception:
                continue  # needs args
            if not url.startswith("/") or url.startswith("/logout"):
                continue
            try:
                resp = self.client.get(url)
            except Exception as e:
                failures.append((key, url, f"EXC {type(e).__name__}: {e}"))
                continue
            if resp.status_code >= 500:
                failures.append((key, url, resp.status_code))
        if failures:
            msg = "\n".join(f"  {k} {u} -> {s}" for k, u, s in failures)
            self.fail(f"{len(failures)} view(s) 500/raised:\n{msg}")
