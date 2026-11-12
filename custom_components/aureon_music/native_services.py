"""Spotify Web API actions used by the AUREON dashboard."""

from __future__ import annotations

import re

import voluptuous as vol
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_platform

SEARCH = {
    "search_tracks": "track",
    "search_albums": "album",
    "search_playlists": "playlist",
    "search_artists": "artist",
}
LIBRARY = {
    "get_track_favorites": "/me/tracks",
    "get_album_favorites": "/me/albums",
    "get_playlist_favorites": "/me/playlists",
    "get_show_favorites": "/me/shows",
    "get_player_recent_tracks": "/me/player/recently-played",
}
FAVORITE = {
    "check_track_favorites": ("GET", "/me/library/contains"),
    "save_track_favorites": ("PUT", "/me/library"),
    "remove_track_favorites": ("DELETE", "/me/library"),
}
READ = {
    *SEARCH,
    *LIBRARY,
    "get_spotify_connect_devices",
    "check_track_favorites",
    "get_queue",
}
WRITE = {
    "save_track_favorites",
    "remove_track_favorites",
    "player_transfer_playback",
    "player_media_play_tracks",
    "player_media_play_context",
    "add_to_queue",
    "playlist_items_add",
}


def register_native_services():
    """Use HA entity service dispatch for target and control permissions."""
    platform = entity_platform.async_get_current_platform()
    for name, actions, handler, response in (
        ("native_read", READ, _read, SupportsResponse.ONLY),
        ("native_write", WRITE, _write, SupportsResponse.NONE),
    ):
        platform.async_register_entity_service(
            name,
            {
                vol.Required("action"): vol.In(actions),
                vol.Optional("arguments", default=dict): dict,
            },
            handler,
            supports_response=response,
        )


def _require_account(entity, call):
    target = call.data.get("entity_id")
    if target != entity.entity_id and target != [entity.entity_id]:
        raise ServiceValidationError("Select exactly one native AUREON Música player")


async def _read(entity, call):
    _require_account(entity, call)
    return {
        "result": await _execute(entity, call.data["action"], call.data["arguments"])
    }


async def _write(entity, call):
    _require_account(entity, call)
    await _execute(entity, call.data["action"], call.data["arguments"])


async def _execute(entity, action, args):
    from .native_media_player import NativeSpotifyPlayer

    if not isinstance(entity, NativeSpotifyPlayer):
        raise ServiceValidationError("Select a native AUREON Música player")
    api = entity.api
    if action in SEARCH or action in LIBRARY:
        search = action in SEARCH
        page_size = 10 if search else 50
        limit = args.get("limit", 10 if search else 20)
        offset = args.get("offset", 0)
        if (
            type(limit) is not int
            or type(offset) is not int
            or not 1 <= limit <= page_size
            or not 0 <= offset <= 10000
        ):
            raise ServiceValidationError("Invalid Spotify page")
        params = {"limit": limit, "offset": offset}
        if search:
            query = args.get("criteria")
            if not isinstance(query, str) or not query.strip() or len(query) > 200:
                raise ServiceValidationError("Invalid Spotify search")
            return await entity._call(
                api.request,
                "GET",
                "/search",
                params={**params, "q": query, "type": SEARCH[action]},
            )
        if action == "get_player_recent_tracks":
            params.pop("offset")
        return await entity._call(api.request, "GET", LIBRARY[action], params=params)
    if action in FAVORITE:
        track = args.get("ids")
        if not isinstance(track, str) or not re.fullmatch(r"[A-Za-z0-9]{1,64}", track):
            raise ServiceValidationError("Invalid Spotify track")
        method, path = FAVORITE[action]
        result = await entity._call(
            api.request, method, path, params={"uris": f"spotify:track:{track}"}
        )
        if action == "check_track_favorites":
            return (
                {track: result[0]}
                if isinstance(result, list) and len(result) == 1
                else {}
            )
        return None
    if action == "get_spotify_connect_devices":
        return {"devices": await entity.async_discover_devices()}
    if action == "get_queue":
        return await entity._call(api.queue)
    if action == "player_transfer_playback":
        device_id = args.get("device_id")
        play = args.get("play", False)
        if type(play) is not bool or not isinstance(device_id, str):
            raise ServiceValidationError("Invalid Spotify device transfer")
        devices = await entity.async_discover_devices()
        device = next((device for device in devices if device["id"] == device_id), None)
        if device is None:
            raise ServiceValidationError("Spotify device unavailable")
        return await entity.async_choose_device(device, play=play)
    if action == "playlist_items_add":
        playlist = args.get("playlist_id")
        uri = args.get("uris")
        if (
            not isinstance(playlist, str)
            or not re.fullmatch(r"[A-Za-z0-9]{1,64}", playlist)
            or not isinstance(uri, str)
            or not re.fullmatch(r"spotify:track:[A-Za-z0-9]{1,64}", uri)
        ):
            raise ServiceValidationError("Invalid Spotify playlist or track")
        return await entity._call(
            api.request, "POST", f"/playlists/{playlist}/items", json={"uris": [uri]}
        )
    if action == "add_to_queue":
        uri = args.get("uri")
        if not isinstance(uri, str):
            raise ServiceValidationError("Invalid Spotify queue item")
        return await entity._control(api.enqueue, uri)
    uri = args.get("uris" if action == "player_media_play_tracks" else "context_uri")
    if not isinstance(uri, str) or not uri:
        raise ServiceValidationError("Invalid Spotify playback item")
    return await entity.async_play_spotify(uri)
