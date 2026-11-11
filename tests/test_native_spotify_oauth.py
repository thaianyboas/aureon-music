"""OAuth refresh transport tests using a fake HTTP session."""
import pytest

from custom_components.aureon_music.native_oauth import SpotifyReauthorizationRequired
from custom_components.aureon_music.native_spotify_oauth import (
    SpotifyOAuthRefresher,
    SpotifyTokenRefreshError,
    TOKEN_URL,
)


class Response:
    def __init__(self, status, body):
        self.status = status
        self.body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def json(self, **_):
        return self.body


class Session:
    def __init__(self, status, body):
        self.response = Response(status, body)
        self.args = None

    def post(self, *args, **kwargs):
        self.args = args, kwargs
        return self.response


@pytest.mark.asyncio
async def test_pkce_refresh_uses_client_id_and_keeps_rotated_token():
    session = Session(200, {"access_token": "new", "refresh_token": "rotated", "expires_in": 3600})
    refreshed = await SpotifyOAuthRefresher(session, "app-id")("old")
    assert refreshed["refresh_token"] == "rotated"
    assert session.args[0] == (TOKEN_URL,)
    assert session.args[1]["data"] == {
        "grant_type": "refresh_token", "refresh_token": "old", "client_id": "app-id"
    }
    assert "Authorization" not in session.args[1]["headers"]


@pytest.mark.asyncio
async def test_confidential_client_uses_basic_auth():
    session = Session(200, {"access_token": "new"})
    await SpotifyOAuthRefresher(session, "app-id", "secret")("old")
    assert session.args[1]["headers"]["Authorization"].startswith("Basic ")
    assert "client_id" not in session.args[1]["data"]


@pytest.mark.asyncio
async def test_revoked_token_requires_reauthorization():
    session = Session(400, {"error": "invalid_grant", "error_description": "sensitive"})
    with pytest.raises(SpotifyReauthorizationRequired) as error:
        await SpotifyOAuthRefresher(session, "app-id")("old")
    assert "sensitive" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body", [
    (429, {"error": "rate_limited"}),
    (503, {"error": "unavailable"}),
    (400, {"error": "invalid_client"}),
])
async def test_other_failures_are_not_mistaken_for_revocation(status, body):
    session = Session(status, body)
    with pytest.raises(SpotifyTokenRefreshError):
        await SpotifyOAuthRefresher(session, "app-id")("old")
