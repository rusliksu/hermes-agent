import importlib.machinery
import sys
from unittest.mock import MagicMock

import pytest

def _gateway_conftest_module():
    for name, module in sys.modules.items():
        if name.endswith("conftest") and hasattr(module, "_ensure_telegram_mock"):
            return module
    raise AssertionError("gateway conftest module was not loaded")


@pytest.mark.skipif(
    importlib.machinery.PathFinder.find_spec("telegram") is None,
    reason="python-telegram-bot is not installed",
)
def test_telegram_bootstrap_keeps_real_ptb_when_available():
    import telegram
    import telegram.request

    assert not isinstance(telegram, MagicMock)
    assert not isinstance(telegram.request.BaseRequest, MagicMock)


def test_telegram_bootstrap_falls_back_to_mock_when_ptb_missing(monkeypatch):
    conftest = _gateway_conftest_module()
    module_names = (
        "telegram",
        "telegram.ext",
        "telegram.constants",
        "telegram.request",
        "telegram.error",
    )
    saved_modules = {
        name: sys.modules[name] for name in module_names if name in sys.modules
    }

    try:
        for name in module_names:
            sys.modules.pop(name, None)

        monkeypatch.setattr(conftest.importlib.util, "find_spec", lambda name: None)

        conftest._ensure_telegram_mock()

        assert isinstance(sys.modules["telegram"], MagicMock)
        assert sys.modules["telegram.request"] is sys.modules["telegram"]
        assert sys.modules["telegram"].constants.ChatType.SUPERGROUP == "supergroup"
    finally:
        for name in module_names:
            sys.modules.pop(name, None)
        sys.modules.update(saved_modules)
