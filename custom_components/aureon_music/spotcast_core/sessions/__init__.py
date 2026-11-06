"""Module containing sessions managers for different usecases

Classes:
    - PublicSession
    - DesktopSession
    - ConnectionSession

Functions:
    - async_get_config_entry_implementation
"""

from custom_components.aureon_music.spotcast_core.sessions.public_session import (
    PublicSession,
    async_get_config_entry_implementation,
)
from custom_components.aureon_music.spotcast_core.sessions.connection_session import (
    ConnectionSession,
)

from custom_components.aureon_music.spotcast_core.sessions.desktop_session import (
    DesktopSession,
)

__all__ = [
    "PublicSession",
    "DesktopSession",
    "ConnectionSession",
    "async_get_config_entry_implementation",
]
