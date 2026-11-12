"""Home Assistant player for a single opt-in Spotify account."""

from __future__ import annotations

import logging
from datetime import timedelta

from aiohttp import ClientError
from homeassistant.components.media_player import MediaPlayerEntity
from homeassistant.components.media_player.const import (
    MediaPlayerEntityFeature,
    MediaPlayerState,
    RepeatMode,
)
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .native_oauth import SpotifyReauthorizationRequired
from .native_services import register_native_services
from .native_spotify_api import SpotifyApiError
from .native_spotify_oauth import SpotifyTokenRefreshError

LOGGER = logging.getLogger(__name__)
TRANSIENT_ERRORS = (
    ClientError,
    TimeoutError,
    OSError,
    SpotifyApiError,
    SpotifyTokenRefreshError,
)


async def async_setup_entry(hass, entry, async_add_entities):
    api = entry.runtime_data

    async def update():
        try:
            return await api.playback() or {}
        except SpotifyReauthorizationRequired:
            raise ConfigEntryAuthFailed(
                "Spotify authorization is no longer valid"
            ) from None
        except TRANSIENT_ERRORS:
            raise UpdateFailed(
                "Spotify playback state temporarily unavailable"
            ) from None

    coordinator = DataUpdateCoordinator(
        hass,
        LOGGER,
        config_entry=entry,
        name=f"Spotify ({entry.entry_id})",
        update_interval=timedelta(seconds=15),
        update_method=update,
    )
    await coordinator.async_config_entry_first_refresh()
    async_add_entities([NativeSpotifyPlayer(entry, coordinator)])
    register_native_services()


class NativeSpotifyPlayer(CoordinatorEntity, MediaPlayerEntity):
    """Reflect Spotify state and send commands only through explicit HA actions."""

    _attr_has_entity_name = True
    _attr_media_image_remotely_accessible = True
    _features = (
        MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
        | MediaPlayerEntityFeature.SEEK
        | MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.SHUFFLE_SET
        | MediaPlayerEntityFeature.REPEAT_SET
        | MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.MEDIA_ENQUEUE
    )

    def __init__(self, entry, coordinator):
        super().__init__(coordinator)
        self.entry = entry
        self.api = entry.runtime_data
        self._attr_unique_id = entry.unique_id
        self._attr_name = entry.data["spotify_display_name"]
        self._attr_extra_state_attributes = {
            "sp_user_id": entry.data.get(
                "spotify_user_id", entry.data["spotify_account_id"]
            ),
            "sp_user_display_name": self._attr_name,
            "aureon_music_native": True,
        }
        self._pending_device = None
        self._apply_state()

    def _apply_state(self):
        playback = self.coordinator.data or {}
        item = playback.get("item") or {}
        album = item.get("album") or item.get("show") or {}
        device = playback.get("device") or {}
        private = device.get("is_private_session", False)
        if private:
            item, album = {}, {}
        self._attr_state = (
            MediaPlayerState.PLAYING
            if playback.get("is_playing")
            else MediaPlayerState.PAUSED
            if item
            else MediaPlayerState.IDLE
        )
        self._attr_media_content_id = item.get("uri")
        self._attr_media_title = item.get("name")
        self._attr_media_artist = ", ".join(
            a["name"] for a in item.get("artists", [])
        ) or album.get("publisher")
        self._attr_media_album_name = album.get("name")
        images = album.get("images") or item.get("images") or []
        self._attr_media_image_url = images[0].get("url") if images else None
        duration = item.get("duration_ms")
        self._attr_media_duration = duration / 1000 if duration is not None else None
        position = None if private else playback.get("progress_ms")
        self._attr_media_position = position / 1000 if position is not None else None
        self._attr_media_position_updated_at = (
            dt_util.utcnow() if position is not None else None
        )
        self._attr_shuffle = playback.get("shuffle_state")
        self._attr_repeat = {
            "track": RepeatMode.ONE,
            "context": RepeatMode.ALL,
            "off": RepeatMode.OFF,
        }.get(playback.get("repeat_state"))
        if (
            self._pending_device
            and device.get("id") == self._pending_device["id"]
            and (playback.get("is_playing") or playback.get("item"))
        ):
            self._pending_device = None
        self._attr_source = (
            self._pending_device["name"] if self._pending_device else device.get("name")
        )
        volume = device.get("volume_percent")
        self._attr_volume_level = volume / 100 if volume is not None else None
        self._attr_supported_features = self._features
        if device.get("supports_volume") and not device.get("is_restricted"):
            self._attr_supported_features |= MediaPlayerEntityFeature.VOLUME_SET

    def _handle_coordinator_update(self):
        self._apply_state()
        super()._handle_coordinator_update()

    async def _call(self, operation, *args, **kwargs):
        try:
            return await operation(*args, **kwargs)
        except SpotifyReauthorizationRequired:
            self.entry.async_start_reauth(self.hass)
            raise ServiceValidationError(
                "Spotify authorization is no longer valid"
            ) from None
        except SpotifyApiError as error:
            if error.status == 403:
                raise ServiceValidationError(
                    "Spotify denied this action for this account or device (403)"
                ) from None
            if error.status == 404:
                raise ServiceValidationError(
                    "Spotify device or item unavailable (404)"
                ) from None
            raise HomeAssistantError(f"Spotify returned HTTP {error.status}") from None
        except TRANSIENT_ERRORS:
            raise HomeAssistantError("Spotify is temporarily unavailable") from None
        except ValueError:
            raise ServiceValidationError("Invalid Spotify playback request") from None

    async def _control(self, operation, *args, **kwargs):
        await self._call(operation, *args, **kwargs)
        await self.coordinator.async_request_refresh()

    async def async_media_play(self):
        await self.async_play_spotify()

    async def async_media_pause(self):
        await self._control(self.api.pause)

    async def async_media_next_track(self):
        await self._control(self.api.next_track)

    async def async_media_previous_track(self):
        await self._control(self.api.previous_track)

    async def async_media_seek(self, position):
        await self._control(self.api.seek, round(position * 1000))

    async def async_set_volume_level(self, volume):
        await self._control(self.api.volume, round(volume * 100))

    async def async_set_shuffle(self, shuffle):
        await self._control(self.api.shuffle, shuffle)

    async def async_set_repeat(self, repeat):
        await self._control(
            self.api.repeat,
            {RepeatMode.ONE: "track", RepeatMode.ALL: "context", RepeatMode.OFF: "off"}[
                repeat
            ],
        )

    async def async_play_media(self, media_type, media_id, **kwargs):
        enqueue = kwargs.get("enqueue")
        if enqueue == "add":
            await self._control(self.api.enqueue, media_id)
            return
        if enqueue not in (None, "play"):
            raise ServiceValidationError(
                "Spotify supports playing now or adding to the end of the queue"
            )
        await self.async_play_spotify(media_id)

    async def async_play_spotify(self, uri=None):
        device_id = self._pending_device["id"] if self._pending_device else None
        await self._control(self.api.play, uri=uri, device_id=device_id)

    async def async_select_source(self, source):
        devices = await self.async_discover_devices()
        device = next((device for device in devices if device["name"] == source), None)
        if device is None:
            raise ServiceValidationError("Spotify device unavailable")
        await self.async_choose_device(device)

    async def async_choose_device(self, device, *, play=False):
        """Set a destination when idle, or transfer the existing playback."""
        playback = self.coordinator.data or {}
        if playback.get("is_playing") or playback.get("item"):
            await self._call(self.api.transfer_playback, device["id"], play=play)
            self._pending_device = None
            await self.coordinator.async_request_refresh()
            return

        self._pending_device = device
        self._attr_source = device["name"]
        self.async_write_ha_state()

    async def async_discover_devices(self):
        result = await self._call(self.api.devices) or {}
        devices = [
            device
            for device in result.get("devices", [])
            if device.get("id")
            and device.get("name")
            and not device.get("is_restricted")
        ]
        devices.sort(key=lambda device: not device.get("is_active", False))
        self._attr_source_list = list(
            dict.fromkeys(device["name"] for device in devices)
        )
        self.async_write_ha_state()
        return devices
