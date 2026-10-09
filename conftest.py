"""pytest + pytest-django — ta sama infrastruktura co ``manage.py test`` (testkit.runner).

    pytest web/ui/tests/test_testkit_infra.py        # konfiguracja + env w pyproject.toml
"""
import pytest


def pytest_configure(config):
    from testkit.runner import prepare
    prepare()


@pytest.fixture
def fake_http():
    from testkit.fake_http import FakeHTTP
    with FakeHTTP() as http:
        yield http


@pytest.fixture
def seed(db):
    from testkit.seed import seed_all
    return seed_all()


@pytest.fixture
def persona_client(db):
    """``persona_client("Transport")`` → zalogowany klient testowy persony."""
    from testkit.personas import client_for
    return client_for
