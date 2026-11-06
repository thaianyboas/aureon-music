# AUREON Música

SpotifyPlus and Spotcast inside AUREON, as one Home Assistant integration (`aureon_music`). The AUREON dashboard uses
it automatically; the original SpotifyPlus and Spotcast integrations are no longer needed.

## Instalar (passo a passo)

1. HACS → ⋮ → **Repositórios personalizados** → `https://github.com/thaianyboas/aureon-music`, tipo **Integração**.
2. Baixe **AUREON Música** e **reinicie** o Home Assistant.
3. Configurações → Dispositivos e serviços → **Adicionar integração** → **AUREON Música**:
   - **Conta do Spotify (SpotifyPlus)** — informe o *Client ID* e *Client Secret* do seu app Spotify Developer
     (os mesmos de antes) e faça login no Spotify;
   - **Tocar Spotify em caixas Cast / Alexa (Spotcast)** — siga os passos da tela (login do Spotify).
4. Confira se o player toca no AUREON. Só então remova SpotifyPlus e Spotcast originais (HACS e integrações).

No app Spotify Developer, o *Redirect URI* continua `https://my.home-assistant.io/redirect/oauth`.

## Para quem mantém

`scripts/build_from_upstream.py <spotifyplus> <spotcast> custom_components/aureon_music` refaz as pastas `*_core`
a partir de uma nova versão dos projetos originais. Licenças e mudanças: `NOTICE.md`, `LICENSE`, `licenses/`.
