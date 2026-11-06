"""System health of both parts of AUREON Música."""
from homeassistant.components.system_health import SystemHealthRegistration
from homeassistant.core import HomeAssistant, callback

from .spotcast_core import system_health as cast_health
from .spotifyplus_core import system_health as plus_health


@callback
def async_register(hass: HomeAssistant, register: SystemHealthRegistration) -> None:
    register.async_register_info(system_health_info)


async def system_health_info(hass: HomeAssistant) -> dict:
    info: dict = {}
    for prefix, module in (("spotifyplus", plus_health), ("spotcast", cast_health)):
        try:
            part = await module.system_health_info(hass)
        except Exception as err:  # noqa: BLE001 - health must never break the page
            part = {"error": str(err)}
        info.update({f"{prefix}_{key}": value for key, value in (part or {}).items()})
    return info
