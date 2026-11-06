"""AUREON Música: SpotifyPlus and Spotcast in one integration.

Each config entry belongs to one part (see const.CONF_KIND); setup, unload and migration go to that part's
original code. Services of both parts are registered under the `aureon_music` domain."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.typing import ConfigType

from . import spotcast_core as _cast_core
from . import spotifyplus_core as _plus_core
from .const import KIND_CAST, entry_kind

CONFIG_SCHEMA = _plus_core.CONFIG_SCHEMA


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    return await _plus_core.async_setup(hass, config)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await (_cast_core if entry_kind(entry) == KIND_CAST else _plus_core).async_setup_entry(hass, entry)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await (_cast_core if entry_kind(entry) == KIND_CAST else _plus_core).async_unload_entry(hass, entry)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry_kind(entry) == KIND_CAST:
        return True
    return await _plus_core.async_migrate_entry(hass, entry)


async def async_remove_config_entry_device(hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry) -> bool:
    if entry_kind(entry) == KIND_CAST:
        return await _cast_core.async_remove_config_entry_device(hass, entry, device)
    return True
