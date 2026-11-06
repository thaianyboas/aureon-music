# """ Intent handlers namespace. """

# import all classes from the namespace.
from .aureon_musicfavoriteaddremove_handler import AureonMusicFavoriteAddRemove_Handler
from .aureon_musicgetinfoartistbio_handler import AureonMusicGetInfoArtistBio_Handler
from .aureon_musicgetnowplayinginfo_handler import AureonMusicGetNowPlayingInfo_Handler
from .aureon_musicplayerdeckcontrol_handler import AureonMusicPlayerDeckControl_Handler
from .aureon_musicplayersetrepeatmode_handler import AureonMusicPlayerSetRepeatMode_Handler
from .aureon_musicplayersetshufflemode_handler import AureonMusicPlayerSetShuffleMode_Handler
from .aureon_musicplayertransferplayback_handler import AureonMusicPlayerTransferPlayback_Handler
from .aureon_musicplayervolumecontrol_handler import AureonMusicPlayerVolumeControl_Handler
from .aureon_musicplaylistcreate_handler import AureonMusicPlaylistCreate_Handler
from .aureon_musicsearchplaycontrol_handler import AureonMusicSearchPlayControl_Handler

# all classes to import when "import *" is specified.
__all__ = [
    'AureonMusicFavoriteAddRemove_Handler',
    'AureonMusicGetInfoArtistBio_Handler',
    'AureonMusicGetNowPlayingInfo_Handler',
    'AureonMusicPlayerDeckControl_Handler',
    'AureonMusicPlayerSetRepeatMode_Handler',
    'AureonMusicPlayerSetShuffleMode_Handler',
    'AureonMusicPlayerTransferPlayback_Handler',
    'AureonMusicPlayerVolumeControl_Handler',
    'AureonMusicPlaylistCreate_Handler',
    'AureonMusicSearchPlayControl_Handler',
]
