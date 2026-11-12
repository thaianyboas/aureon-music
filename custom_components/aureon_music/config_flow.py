"""Set up native Spotify accounts and reauthenticate existing entries."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.core import callback

from .const import CONF_KIND, DOMAIN, KIND_CAST, KIND_NATIVE, KIND_PLUS
from .native_config_flow import NativeSpotifyFlow
from .spotcast_core.config_flow_classes.config_flow_handler import SpotcastFlowHandler
from .spotcast_core.config_flow_classes.options_flow_handler import SpotcastOptionsFlowHandler
from .spotifyplus_core.config_flow import AureonMusicConfigFlow as SpotifyPlusFlow
from .spotifyplus_core.config_flow import AureonMusicOptionsFlow as SpotifyPlusOptionsFlow


def _with_kind(base, kind: str):
    """A subclass of an original flow whose entries remember which part of AUREON Música they belong to.
    Defined without a mixin to preserve the object layout when switching flows."""

    def async_create_entry(self, *, data: Mapping[str, Any] | None = None, **kwargs):
        return base.async_create_entry(self, data={**(data or {}), CONF_KIND: kind}, **kwargs)

    def async_update_reload_and_abort(self, entry: ConfigEntry, *args, **kwargs):
        for key in ("data", "data_updates"):
            if kwargs.get(key) is not None:
                kwargs[key] = {**kwargs[key], CONF_KIND: kind}
        return base.async_update_reload_and_abort(self, entry, *args, **kwargs)

    return type(base.__name__.replace("AureonMusic", "SpotifyPlus") + "ForAureon", (base,), {"DOMAIN": DOMAIN, "AUREON_KIND": kind, "async_create_entry": async_create_entry, "async_update_reload_and_abort": async_update_reload_and_abort, "__module__": __name__})


PlusFlow = _with_kind(SpotifyPlusFlow, KIND_PLUS)
CastFlow = _with_kind(SpotcastFlowHandler, KIND_CAST)


class AureonMusicFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Start native setup directly and retain reauthentication for existing entries."""

    VERSION = 2
    MINOR_VERSION = 1

    def _become(self, cls):
        state = dict(self.__dict__)
        self.__class__ = cls
        cls.__init__(self)
        self.__dict__.update(state)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._become(NativeSpotifyFlow)
        return await self.async_step_native(user_input)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        if entry_data.get(CONF_KIND) == KIND_NATIVE:
            self._become(NativeSpotifyFlow)
            return await self.async_step_native_reauth()
        self._become(CastFlow if entry_data.get(CONF_KIND) == KIND_CAST else PlusFlow)
        step = getattr(self, "async_step_reauth", None)
        if step is not None:
            return await step(entry_data)
        return await self.async_step_reauth_confirm()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry):
        if config_entry.data.get(CONF_KIND) == KIND_NATIVE:
            return None
        if config_entry.data.get(CONF_KIND) == KIND_CAST:
            return SpotcastFlowHandler.async_get_options_flow(config_entry)
        return SpotifyPlusFlow.async_get_options_flow(config_entry)
