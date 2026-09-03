import pytest

from src.settings import ConfigurationError, load_settings


def test_watchlist_requires_history() -> None:
    with pytest.raises(ConfigurationError, match="必须同时启用 DMF 历史功能"):
        load_settings(
            {
                "DMF_WATCHLIST_ENABLED": "true",
                "DMF_HISTORY_ENABLED": "false",
            },
            require_token=False,
        )


def test_watchlist_poll_interval_is_configurable() -> None:
    settings = load_settings(
        {
            "DMF_WATCHLIST_ENABLED": "true",
            "DMF_HISTORY_ENABLED": "true",
            "DMF_WATCHLIST_POLL_SECONDS": "30",
        },
        require_token=False,
    )

    assert settings.dmf_watchlist_enabled is True
    assert settings.dmf_watchlist_poll_seconds == 30