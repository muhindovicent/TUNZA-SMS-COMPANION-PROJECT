import base64

import pytest

from tunza.config import ConfigurationError, build_config


def test_transfer_fixture_mode_is_off_by_default(settings):
    assert build_config(settings)["SYNTHETIC_TRANSFERS_ENABLED"] is False


def test_transfer_fixture_mode_can_be_explicitly_enabled_for_tests(settings):
    assert (
        build_config({**settings, "SYNTHETIC_TRANSFERS_ENABLED": True})[
            "SYNTHETIC_TRANSFERS_ENABLED"
        ]
        is True
    )


def test_production_cannot_enable_synthetic_transfers(settings):
    with pytest.raises(ConfigurationError, match="cannot be enabled in production"):
        build_config(
            {
                **settings,
                "APP_ENV": "production",
                "SYNTHETIC_TRANSFERS_ENABLED": True,
                "PATIENT_ENCRYPTION_KEY": base64.urlsafe_b64encode(b"s" * 32).decode(),
                "PATIENT_KEY_VERSION": "synthetic-v1",
            }
        )


@pytest.mark.parametrize(
    "path,method",
    [
        ("/transfers/capabilities", "GET"),
        ("/transfers", "POST"),
        ("/transfers?facility_id=synthetic", "GET"),
        ("/transfers/destinations", "GET"),
    ],
)
def test_transfer_endpoints_require_authentication_without_database(client, path, method):
    response = client.open("/api/v1" + path, method=method)
    assert response.status_code in {401, 403}
