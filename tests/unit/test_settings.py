"""Unit tests untuk modul settings dan validasi environment variables Iwed."""

from typing import Any

import pytest
from pydantic import ValidationError

from iwed_bot.settings import Settings


def test_valid_settings_creation(valid_env_dict: dict[str, Any]) -> None:
    """Memverifikasi bahwa Settings berhasil dibuat dengan konfigurasi yang valid."""
    settings = Settings(_env_file=None, **valid_env_dict)

    assert settings.DISCORD_APPLICATION_ID == 123456789012345678
    assert settings.DISCORD_TEST_GUILD_ID == 987654321098765432
    assert settings.LAVALINK_URI == "http://localhost:2333"
    assert settings.LOG_LEVEL == "DEBUG"
    assert settings.DISCORD_TOKEN.get_secret_value() == "test_mock_discord_token_secret_123456"
    assert settings.LAVALINK_PASSWORD.get_secret_value() == "test_lavalink_password_xyz"


def test_optional_test_guild_id(valid_env_dict: dict[str, Any]) -> None:
    """Memverifikasi bahwa DISCORD_TEST_GUILD_ID bersifat opsional (dapat bernilai None)."""
    data = dict(valid_env_dict)
    data["DISCORD_TEST_GUILD_ID"] = None
    settings = Settings(_env_file=None, **data)
    assert settings.DISCORD_TEST_GUILD_ID is None

    # Menguji nilai tidak valid (<= 0)
    data["DISCORD_TEST_GUILD_ID"] = -1
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **data)


def test_secret_masking(valid_settings: Settings) -> None:
    """Memverifikasi bahwa token dan password rahasia tidak pernah bocor ke publik."""
    token_raw = "test_mock_discord_token_secret_123456"
    pass_raw = "test_lavalink_password_xyz"

    repr_str = repr(valid_settings)
    str_val = str(valid_settings)
    summary = valid_settings.safe_summary()

    # Pastikan raw secrets tidak muncul di representasi string
    assert token_raw not in repr_str
    assert pass_raw not in repr_str
    assert token_raw not in str_val
    assert pass_raw not in str_val

    # Pastikan safe_summary menyamarkan secrets
    assert summary["DISCORD_TOKEN"] == "**********"
    assert summary["LAVALINK_PASSWORD"] == "**********"


def test_redis_url_credential_masking(valid_env_dict: dict[str, Any]) -> None:
    """Memverifikasi bahwa kredensial pada REDIS_URL tidak bocor ke repr, str, atau safe_summary."""
    secret_user = "admin_user_secret"
    secret_pass = "super_secret_redis_pass_xyz999"
    credentialed_url = f"redis://{secret_user}:{secret_pass}@redis.example.com:6379/0"

    data = dict(valid_env_dict)
    data["QUEUE_BACKEND"] = "redis"
    data["REDIS_URL"] = credentialed_url

    settings = Settings(_env_file=None, **data)

    repr_str = repr(settings)
    str_str = str(settings)
    summary = settings.safe_summary()

    # Pastikan username & password tidak pernah muncul
    assert secret_user not in repr_str
    assert secret_pass not in repr_str
    assert secret_user not in str_str
    assert secret_pass not in str_str
    assert secret_user not in str(summary)
    assert secret_pass not in str(summary)

    # Pastikan safe_summary menyamarkan nilai menjadi '<configured>'
    assert summary["REDIS_URL"] == "<configured>"


def test_redis_url_validation_error_leakage_regression(valid_env_dict: dict[str, Any]) -> None:
    """Regression test: kredensial pada REDIS_URL invalid tidak bocor di ValidationError."""
    secret_user = "admin_user"
    secret_pass = "super_secret_password"
    invalid_url = f"http://{secret_user}:{secret_pass}@redis.example.com:6379/0"

    data = dict(valid_env_dict)
    data["QUEUE_BACKEND"] = "redis"
    data["REDIS_URL"] = invalid_url

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None, **data)

    err_str = str(exc_info.value)
    # Pastikan username dan password tidak muncul dalam str(ValidationError)
    assert secret_user not in err_str
    assert secret_pass not in err_str

    # Pastikan username dan password tidak muncul dalam setiap msg dari errors()
    for err in exc_info.value.errors():
        msg_str = err.get("msg", "")
        assert secret_user not in msg_str
        assert secret_pass not in msg_str
        assert "REDIS_URL harus menggunakan skema redis:// atau rediss://." in msg_str


def test_lavalink_password_default_consistency() -> None:
    """Memverifikasi bahwa default password Lavalink pada settings adalah 'youshallnotpass'."""
    data = {
        "DISCORD_TOKEN": "mock_token",
        "DISCORD_APPLICATION_ID": 12345,
    }
    settings = Settings(_env_file=None, **data)
    assert settings.LAVALINK_PASSWORD.get_secret_value() == "youshallnotpass"


def test_missing_mandatory_discord_token(valid_env_dict: dict[str, Any]) -> None:
    """Memverifikasi kegagalan validasi cepat jika DISCORD_TOKEN tidak disediakan."""
    data = dict(valid_env_dict)
    del data["DISCORD_TOKEN"]
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None, **data)
    assert any("DISCORD_TOKEN" in str(loc) for err in exc_info.value.errors() for loc in err["loc"])


def test_missing_mandatory_application_id(valid_env_dict: dict[str, Any]) -> None:
    """Memverifikasi kegagalan validasi jika DISCORD_APPLICATION_ID hilang."""
    data = dict(valid_env_dict)
    del data["DISCORD_APPLICATION_ID"]
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **data)


@pytest.mark.parametrize(
    "valid_uri",
    [
        "http://localhost:2333",
        "http://127.0.0.1:2333",
        "http://lavalink:2333",
        "https://lavalink.example.com",
        "https://lavalink.example.com:443",
    ],
)
def test_valid_lavalink_uri(valid_env_dict: dict[str, Any], valid_uri: str) -> None:
    """Memverifikasi penerimaan URI Lavalink HTTP dan HTTPS yang valid."""
    data = dict(valid_env_dict)
    data["LAVALINK_URI"] = valid_uri
    settings = Settings(_env_file=None, **data)
    assert valid_uri == settings.LAVALINK_URI


@pytest.mark.parametrize(
    "invalid_uri",
    [
        "ws://localhost:2333",
        "wss://localhost:2333",
        "ftp://localhost:2333",
        "http://",
        "https://",
        "invalid-plain-text",
    ],
)
def test_invalid_lavalink_uri_scheme(valid_env_dict: dict[str, Any], invalid_uri: str) -> None:
    """Memverifikasi bahwa skema URI Lavalink selain http/https atau tanpa host ditolak."""
    data = dict(valid_env_dict)
    data["LAVALINK_URI"] = invalid_uri
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **data)


@pytest.mark.parametrize("invalid_volume", [-1, 101, 200])
def test_invalid_volume_range(valid_env_dict: dict[str, Any], invalid_volume: int) -> None:
    """Memverifikasi penolakan volume di luar batas aman 0-100."""
    data = dict(valid_env_dict)
    data["DEFAULT_VOLUME"] = invalid_volume
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **data)


@pytest.mark.parametrize("invalid_limit", [0, -10, 5001])
def test_invalid_max_playlist_tracks(valid_env_dict: dict[str, Any], invalid_limit: int) -> None:
    """Memverifikasi batasan kapasitas impor playlist."""
    data = dict(valid_env_dict)
    data["MAX_PLAYLIST_TRACKS"] = invalid_limit
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **data)


@pytest.mark.parametrize("invalid_idle", [0, 5, 90000])
def test_invalid_idle_disconnect_seconds(valid_env_dict: dict[str, Any], invalid_idle: int) -> None:
    """Memverifikasi batasan timeout ketidakaktifan."""
    data = dict(valid_env_dict)
    data["IDLE_DISCONNECT_SECONDS"] = invalid_idle
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **data)


def test_redis_backend_requires_redis_url(valid_env_dict: dict[str, Any]) -> None:
    """Memverifikasi bahwa QUEUE_BACKEND='redis' mewajibkan REDIS_URL terisi."""
    data = dict(valid_env_dict)
    data["QUEUE_BACKEND"] = "redis"
    data["REDIS_URL"] = ""

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None, **data)
    assert "REDIS_URL wajib diisi" in str(exc_info.value)


def test_redis_backend_with_valid_redis_url(valid_env_dict: dict[str, Any]) -> None:
    """Memverifikasi bahwa QUEUE_BACKEND='redis' dengan REDIS_URL valid berhasil diproses."""
    data = dict(valid_env_dict)
    data["QUEUE_BACKEND"] = "redis"
    data["REDIS_URL"] = "redis://127.0.0.1:6379/0"

    settings = Settings(_env_file=None, **data)
    assert settings.QUEUE_BACKEND == "redis"
    assert settings.REDIS_URL == "redis://127.0.0.1:6379/0"


def test_redis_backend_with_invalid_scheme(valid_env_dict: dict[str, Any]) -> None:
    """Memverifikasi bahwa REDIS_URL dengan skema selain redis:// atau rediss:// ditolak."""
    data = dict(valid_env_dict)
    data["QUEUE_BACKEND"] = "redis"
    data["REDIS_URL"] = "http://localhost:6379"

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None, **data)
    assert "REDIS_URL harus menggunakan skema redis:// atau rediss://." in str(exc_info.value)
