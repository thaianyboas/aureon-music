"""AUREON Música: SpotifyPlus and Spotcast in one integration.

Each config entry belongs to one part (see const.CONF_KIND); setup, unload and migration go to that part's
original code. Services of both parts are registered under the `aureon_music` domain."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.typing import ConfigType

from . import spotcast_core as _cast_core
from . import spotifyplus_core as _plus_core
from .const import KIND_CAST, KIND_NATIVE, entry_kind
from .native_ha_adapter import NativeTokenStore, async_create_native_spotify_client
from .native_oauth import SpotifyReauthorizationRequired

CONFIG_SCHEMA = _plus_core.CONFIG_SCHEMA


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    return await _plus_core.async_setup(hass, config)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry_kind(entry) == KIND_NATIVE:
        try:
            entry.runtime_data = await async_create_native_spotify_client(
                hass, entry, client_id=entry.data["client_id"]
            )
        except SpotifyReauthorizationRequired:
            raise ConfigEntryAuthFailed("Spotify authorization is missing") from None
        except OSError:
            raise ConfigEntryNotReady("Could not read Spotify credentials") from None
        await hass.config_entries.async_forward_entry_setups(entry, [Platform.MEDIA_PLAYER])
        return True
    return await (_cast_core if entry_kind(entry) == KIND_CAST else _plus_core).async_setup_entry(hass, entry)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry_kind(entry) == KIND_NATIVE:
        return await hass.config_entries.async_unload_platforms(entry, [Platform.MEDIA_PLAYER])
    return await (_cast_core if entry_kind(entry) == KIND_CAST else _plus_core).async_unload_entry(hass, entry)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry_kind(entry) in {KIND_CAST, KIND_NATIVE}:
        return True
    return await _plus_core.async_migrate_entry(hass, entry)


async def async_remove_config_entry_device(hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry) -> bool:
    if entry_kind(entry) == KIND_CAST:
        return await _cast_core.async_remove_config_entry_device(hass, entry, device)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove only the credential file belonging to the deleted native entry."""
    if entry_kind(entry) == KIND_NATIVE:
        await NativeTokenStore(hass, entry.data.get("credential_key", entry.entry_id)).async_remove()
