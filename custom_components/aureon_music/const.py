"""AUREON Música: one integration holding SpotifyPlus (Spotify account control) and Spotcast (start Spotify on
Cast / Alexa devices). Each config entry is one of the two kinds; everything is dispatched by that kind."""
DOMAIN = "aureon_music"
CONF_KIND = "aureon_kind"
KIND_PLUS = "spotifyplus"
KIND_CAST = "spotcast"


def entry_kind(entry) -> str:
    return (entry.data or {}).get(CONF_KIND, KIND_PLUS)
