"""Home Assistant player for a single opt-in Spotify account."""

from __future__ import annotations

import asyncio
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
from homeassistant.helpers import entity_registry as er
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
PLAYBACK_GATEWAY_ERRORS = {502, 503, 504}
TRANSIENT_ERRORS = (
    ClientError,
    TimeoutError,
    OSError,
    SpotifyApiError,
    SpotifyTokenRefreshError,
)


def _label(item):
    """'Song Artist' (or an album or playlist name) to find the item again by voice search."""
    if not isinstance(item, dict) or not item.get("name"):
        return None
    artists = [
        a.get("name")
        for a in item.get("artists") or []
        if isinstance(a, dict) and a.get("name")
    ]
    return f"{item['name']} {artists[0]}" if artists else item["name"]


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
        """Play resumes the last song, also when no speaker is active (owner 2026-11-12).

        Spotify keeps a paused song (for example on the phone) without an active device and then answers Play with
        404. The paused song moves with its position to the chosen speaker; without one, the last played song
        starts there inside its album or playlist.
        """
        playback = await self._call(self.api.playback) or {}
        device = playback.get("device") or {}
        if (
            device.get("is_active")
            and (playback.get("is_playing") or playback.get("item"))
            and self._pending_device is None
        ):
            await self._resume_on(device)
            return
        target = await self.async_target_device()
        if (
            playback.get("item")
            and device.get("is_active")
            and (target is None or target["id"] == device.get("id"))
        ):
            await self._resume_on(target or device)
            return
        if target is None:
            raise ServiceValidationError(
                "No Spotify speaker is active. Choose a speaker, then press Play."
            )
        # the paused song keeps its place: hand the session to the speaker and play
        if playback.get("item") and await self._try_playback(
            self.api.transfer_playback, target, play=True
        ):
            self._pending_device = target
            await self.coordinator.async_request_refresh()
            return
        recent = await self._call(
            self.api.request, "GET", "/me/player/recently-played", params={"limit": 1}
        )
        items = (recent or {}).get("items", [])
        track = (items[0].get("track") or {}).get("uri") if items else None
        context = (
            (items[0].get("context") or {}).get("uri") if items else None
        ) or None
        if not track:
            raise ServiceValidationError("Choose a song or playlist to start Spotify")
        await self.async_start_on(
            target, track, context, label=_label(items[0].get("track"))
        )

    async def _resume_on(self, target):
        if not await self._try_playback(self.api.play, target):
            raise ServiceValidationError(
                "Spotify could not resume playback. Choose a song or playlist."
            )
        await self.coordinator.async_request_refresh()

    async def _try_playback(self, operation, target, **kwargs):
        """Retry one ambiguous gateway failure on the same confirmed destination."""
        device_id = target.get("id")
        try:
            await operation(device_id=device_id, **kwargs)
        except SpotifyApiError as error:
            self._last_status = error.status
            if error.status not in PLAYBACK_GATEWAY_ERRORS or not device_id:
                return await self._unless_404(self._fail, error)

            await asyncio.sleep(0.5)
            playback = await self._call(self.api.playback) or {}
            device = playback.get("device") or {}
            item = playback.get("item") or {}
            context = playback.get("context") or {}
            expected = kwargs.get("offset_uri") or kwargs.get("uri")
            if (
                device.get("id") == device_id
                and playback.get("is_playing")
                and (
                    expected is None
                    or expected in (item.get("uri"), context.get("uri"))
                )
            ):
                return True

            current = self._find_device(await self.async_discover_devices(), target)
            if current is None:
                return False
            try:
                return await self._unless_404(
                    operation, device_id=current["id"], **kwargs
                )
            except TRANSIENT_ERRORS as retry_error:
                await self._call(self._fail, retry_error)
        except (SpotifyReauthorizationRequired, *TRANSIENT_ERRORS, ValueError) as error:
            await self._call(self._fail, error)
        return True

    async def _unless_404(self, operation, *args, **kwargs):
        """Run a Spotify command; False when Spotify answers 404 (speaker asleep), other errors as usual."""
        failure = None
        try:
            await operation(*args, **kwargs)
        except SpotifyApiError as error:
            self._last_status = error.status
            if error.status == 404:
                return False
            failure = error
        except SpotifyReauthorizationRequired:
            await self._call(operation, *args, **kwargs)
        if failure is not None:
            await self._call(self._fail, failure)
        return True

    @staticmethod
    async def _fail(error):
        """Raise a Spotify error again inside _call, which turns it into a clear message."""
        raise error

    async def async_target_device(self):
        """The chosen speaker as Spotify lists it now; else the only active one; else None."""
        devices = await self.async_discover_devices()
        if selected := self._pending_device:
            device = self._find_device(devices, selected)
            if device is None:
                raise ServiceValidationError(
                    "Selected Spotify speaker is unavailable. Choose the speaker again."
                )
            self._pending_device = device
            return device
        return next((d for d in devices if d.get("is_active")), None)

    @staticmethod
    def _find_device(devices, selected):
        if device := next((d for d in devices if d["id"] == selected["id"]), None):
            return device
        matches = [d for d in devices if d["name"] == selected["name"]]
        return matches[0] if len(matches) == 1 else None

    async def async_start_on(self, target, uri, context=None, label=None):
        """Start playback on the selected Connect device."""

        async def play(device):
            if (
                context
                and context.startswith(("spotify:album:", "spotify:playlist:"))
                and await self._try_playback(
                    self.api.play, device, uri=context, offset_uri=uri
                )
            ):
                return True
            return await self._try_playback(self.api.play, device, uri=uri)

        if not await play(target):
            current = self._find_device(await self.async_discover_devices(), target)
            if current is not None:
                target = current
            transferred = await self._unless_404(
                self.api.transfer_playback, target["id"], play=False
            )
            # Connect may advertise a new ID while applying the transfer.
            for delay in (0.25, 0.75) if transferred else (0.25,):
                await asyncio.sleep(delay)
                current = self._find_device(await self.async_discover_devices(), target)
                if current is None or (
                    not transferred and current["id"] == target["id"]
                ):
                    continue
                target = current
                if await play(target):
                    break
            else:
                # Owner 2026-11-13 ("corrija esse problema de vez"): Amazon speakers and groups often refuse
                # Spotify's Web API until Alexa itself starts Spotify on them. With Alexa Media Player installed,
                # AUREON asks Alexa to play the song there, then takes over the now awake Connect session.
                woken = await self._wake_with_alexa(target, uri, context, label)
                if woken is None:
                    raise ServiceValidationError(
                        f"Spotify could not start playback on {target['name']}"
                        f"{self._status_note()}. Amazon speakers accept Spotify from Home "
                        "Assistant only after Alexa has started Spotify on them: install "
                        "Alexa Media Player (HACS) so AUREON can wake them, or say "
                        "'Alexa, play Spotify' on that speaker once."
                    )
                target = woken
        self._pending_device = target
        await self.coordinator.async_request_refresh()

    def _status_note(self):
        status = getattr(self, "_last_status", None)
        return f" (Spotify HTTP {status})" if status else ""

    def _alexa_entity(self, name):
        """The Alexa Media Player entity of a Spotify Connect speaker or group, matched by its name."""
        wanted = name.casefold().strip()
        registry = er.async_get(self.hass)
        for entry in registry.entities.values():
            if (
                entry.domain != "media_player"
                or entry.platform != "alexa_media"
                or entry.disabled_by
            ):
                continue
            state = self.hass.states.get(entry.entity_id)
            names = {
                entry.name,
                entry.original_name,
                state and state.attributes.get("friendly_name"),
            }
            if wanted in {str(n).casefold().strip() for n in names if n}:
                return entry.entity_id
        return None

    async def _wake_with_alexa(self, target, uri, context, label):
        """Let Alexa start Spotify on the speaker, wait for the Connect session, then play the exact item there.

        Returns the speaker as Spotify then lists it, or None when there is no Alexa Media Player entity for it or
        the speaker did not come up.
        """
        entity_id = self._alexa_entity(target["name"])
        if entity_id is None:
            return None
        phrase = label or await self._label_for(uri or context)
        if not phrase:
            return None
        LOGGER.info(
            "Waking %s through Alexa Media Player (%s)", target["name"], entity_id
        )
        await self.hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": entity_id,
                "media_content_type": "SPOTIFY",
                "media_content_id": phrase,
            },
            blocking=True,
        )
        for _ in range(12):
            await asyncio.sleep(1)
            current = self._find_device(await self.async_discover_devices(), target)
            if current is not None and current.get("is_active"):
                break
        else:
            return None
        # Alexa found the song by its name; the exact song inside its playlist follows on the awake speaker
        exact = (
            (context, uri)
            if context and context.startswith(("spotify:album:", "spotify:playlist:"))
            else (uri, None)
        )
        if exact[0]:
            await self._unless_404(
                self.api.play,
                uri=exact[0],
                offset_uri=exact[1],
                device_id=current["id"],
            )
        return current

    async def _label_for(self, uri):
        """A search phrase for Alexa: a song with its artist, or the name of an album or playlist."""
        if not uri or uri.count(":") != 2:
            return None
        kind, ident = uri.split(":")[1:]
        if kind not in ("track", "album", "playlist", "artist"):
            return None
        try:
            item = await self.api.request("GET", f"/{kind}s/{ident}")
        except (SpotifyApiError, *TRANSIENT_ERRORS[:3], ValueError):
            return None
        return _label(item)

    async def async_media_pause(self):
        # nothing playing anywhere (Spotify answers 404): there is nothing to pause, so no error either
        await self._unless_404(self.api.pause)
        await self.coordinator.async_request_refresh()

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
        if self._pending_device:
            target = await self.async_target_device()
            await self.async_start_on(target, uri)
            return
        await self._control(self.api.play, uri=uri)

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
