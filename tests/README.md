# Tests

`test_token_rotation.py` checks that a refreshed Spotify token is stored before the next refresh, so a rotated
refresh token is never reused (that caused "Authentication expired" repairs several times a day).

Run with Home Assistant's test tools (`pytest-homeassistant-custom-component`) plus the integration's
requirements from `manifest.json` installed, from the repository root:

    PYTHONPATH=. pytest -q tests -o asyncio_mode=auto
