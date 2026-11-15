import base64

import pytest

from tunza.config import ConfigurationError, build_config
from tunza.fixture_database import provision_fixture_role


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
    "override",
    [
        {"DATABASE_RUNTIME_ROLE": "tunza_fixture_runtime"},
        {"DATABASE_URL": "postgresql+psycopg://tunza_fixture_runtime@127.0.0.1/tunza_unit_test"},
    ],
)
def test_production_cannot_use_fixture_database_credentials(settings, override):
    with pytest.raises(ConfigurationError, match="fixture database principal"):
        build_config(
            {
                **settings,
                "APP_ENV": "production",
                "SYNTHETIC_TRANSFERS_ENABLED": False,
                "PATIENT_ENCRYPTION_KEY": base64.urlsafe_b64encode(b"s" * 32).decode(),
                "PATIENT_KEY_VERSION": "synthetic-v1",
                **override,
            }
        )


@pytest.mark.parametrize(
    "url,environment",
    [
        ("postgresql+psycopg://owner@127.0.0.1/tunza_fixture_test", "production"),
        ("postgresql+psycopg://owner@remote.example/tunza_fixture_test", "testing"),
        ("postgresql+psycopg://owner@127.0.0.1/tunza_production", "development"),
        ("postgresql+psycopg://tunza_runtime@127.0.0.1/tunza_fixture_test", "testing"),
        ("postgresql+psycopg://tunza_fixture_runtime@127.0.0.1/tunza_fixture_test", "testing"),
    ],
)
def test_fixture_provisioning_refuses_inappropriate_targets_without_connecting(url, environment):
    with pytest.raises(RuntimeError, match="separate owner and isolated loopback"):
        provision_fixture_role(url, environment=environment)


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
