"""Platform 'binary_sensor' of AUREON Música: handed to the part that owns the config entry."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import KIND_CAST, entry_kind


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    if entry_kind(entry) == KIND_CAST:
        from .spotcast_core.binary_sensor import async_setup_entry as setup
    else:
        return  # only the Spotcast part has binary_sensors
    await setup(hass, entry, async_add_entities)
