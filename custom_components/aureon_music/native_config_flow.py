"""Opt-in Spotify authorization using Home Assistant's signed OAuth callback."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import secrets
import time
from urllib.parse import urlsplit

import voluptuous as vol
from aiohttp import ClientError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import (
    AbstractOAuth2FlowHandler,
    LocalOAuth2Implementation,
)

from .const import CONF_KIND, DOMAIN, KIND_NATIVE
from .native_ha_adapter import NativeTokenStore
from .native_spotify_api import NativeSpotifyApi, SpotifyApiError
from .native_spotify_oauth import TOKEN_URL

SCOPES = (
    "user-read-playback-state user-modify-playback-state user-read-private "
    "playlist-read-private playlist-read-collaborative user-library-read "
    "user-library-modify user-read-recently-played playlist-modify-private playlist-modify-public"
)


class SpotifyPKCE(LocalOAuth2Implementation):
    """Keep the verifier in this flow and exchange the code directly with Spotify."""

    def __init__(self, hass, client_id: str) -> None:
        super().__init__(
            hass,
            DOMAIN,
            client_id,
            "",
            "https://accounts.spotify.com/authorize",
            TOKEN_URL,
        )
        self.verifier = secrets.token_urlsafe(64)

    @property
    def redirect_uri(self) -> str:
        uri = super().redirect_uri
        parsed = urlsplit(uri)
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "::1"}
        ):
            raise RuntimeError("Spotify requires a secure OAuth callback")
        return uri

    @property
    def extra_authorize_data(self) -> dict:
        digest = hashlib.sha256(self.verifier.encode("ascii")).digest()
        return {
            "code_challenge": base64.urlsafe_b64encode(digest)
            .decode("ascii")
            .rstrip("="),
            "code_challenge_method": "S256",
            "scope": SCOPES,
        }

    async def async_resolve_external_data(self, external_data: dict) -> dict:
        session = async_get_clientsession(self.hass)
        async with session.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": external_data["code"],
                "redirect_uri": external_data["state"]["redirect_uri"],
                "client_id": self.client_id,
                "code_verifier": self.verifier,
            },
            allow_redirects=False,
        ) as response:
            if response.status != 200:
                raise ValueError("Spotify rejected the authorization")
            return await response.json(content_type=None)


class NativeSpotifyFlow(AbstractOAuth2FlowHandler):
    """Set up a separate native account and verify its identity during reauth."""

    DOMAIN = DOMAIN
    VERSION = 2
    MINOR_VERSION = 1

    @property
    def logger(self) -> logging.Logger:
        return logging.getLogger(__name__)

    async def async_step_native(self, user_input=None):
        if user_input is None:
            return self.async_show_form(
                step_id="native",
                data_schema=vol.Schema(
                    {vol.Required("client_id"): vol.Match(r"^[a-fA-F0-9]{32}$")}
                ),
            )
        self.flow_impl = SpotifyPKCE(self.hass, user_input["client_id"])
        return await self._async_begin()

    async def async_step_native_reauth(self, user_input=None):
        if user_input is None:
            return self.async_show_form(step_id="native_reauth")
        self.flow_impl = SpotifyPKCE(
            self.hass, self._get_reauth_entry().data["client_id"]
        )
        return await self._async_begin()

    async def _async_begin(self):
        try:
            return await self.async_step_auth()
        except RuntimeError:
            return self.async_abort(reason="native_callback_unavailable")

    async def async_step_creation(self, user_input=None):
        """Validate the grant before persisting any secrets."""
        external_data, self.external_data = self.external_data, None
        if not external_data or not external_data.get("code"):
            return self.async_abort(reason="native_oauth_failed")
        try:
            async with asyncio.timeout(15):
                token = await self.flow_impl.async_resolve_external_data(external_data)
                if not all(
                    isinstance(token.get(key), str) and token[key]
                    for key in ("access_token", "refresh_token")
                ):
                    raise ValueError
                expires_in = int(token["expires_in"])
                if expires_in <= 0:
                    raise ValueError
                token["expires_at"] = time.time() + expires_in
                profile = await self._async_profile(token)
                account = profile.get("account_id")
                if not isinstance(account, str) or not re.fullmatch(
                    r"[a-zA-Z0-9_-]+", account
                ):
                    raise ValueError
        except (
            ClientError,
            TimeoutError,
            SpotifyApiError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
        ):
            return self.async_abort(reason="native_oauth_failed")

        reauth = self.context.get("source") == "reauth"
        if reauth:
            entry = self._get_reauth_entry()
            if account != entry.data["spotify_account_id"]:
                return self.async_abort(reason="wrong_account")
        else:
            await self.async_set_unique_id(f"native:{account}")
            self._abort_if_unique_id_configured()

        key = secrets.token_hex(16)
        store = NativeTokenStore(self.hass, key)
        try:
            await store.async_save(token)
        except OSError:
            return self.async_abort(reason="native_storage_error")
        name = profile.get("display_name") or account
        data = {
            CONF_KIND: KIND_NATIVE,
            "client_id": self.flow_impl.client_id,
            "credential_key": key,
            "spotify_account_id": account,
            "spotify_user_id": profile.get("id") or account,
            "spotify_display_name": name,
        }
        if reauth:
            old_key = entry.data.get("credential_key", entry.entry_id)
            self.hass.config_entries.async_update_entry(entry, data=data)
            if await self.hass.config_entries.async_reload(entry.entry_id):
                try:
                    await NativeTokenStore(self.hass, old_key).async_remove()
                except OSError:
                    self.logger.warning(
                        "Could not remove previous Spotify credential file"
                    )
            return self.async_abort(reason="reauth_successful")
        return self.async_create_entry(title=name, data=data)

    async def _async_profile(self, token: dict) -> dict:
        class InitialGrant:
            async def access_token(
                self, *, force_refresh=False, rejected_access_token=None
            ):
                if force_refresh:
                    raise SpotifyApiError(401)
                return token["access_token"]

        session = async_get_clientsession(self.hass)
        return await NativeSpotifyApi(session, InitialGrant()).profile()
