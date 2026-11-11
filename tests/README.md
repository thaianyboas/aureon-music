# Tests

`test_token_rotation.py` checks that a refreshed Spotify token is stored before the next refresh, so a rotated
refresh token is never reused (that caused "Authentication expired" repairs several times a day).

Run with Home Assistant's test tools (`pytest-homeassistant-custom-component`) plus the integration's
requirements from `manifest.json` installed, from the repository root:

    PYTHONPATH=. pytest -q tests -o asyncio_mode=auto

O smoke test independente roda com `python scripts/check_native_core.py`. A suíte completa também requer os
pacotes declarados nos manifests do Spotify e Cast da versão do Home Assistant usada no teste, além dos pacotes
do manifest AUREON Música. `test_native_home_assistant.py` usa uma instância descartável do Home Assistant e
respostas simuladas do Spotify para validar PKCE, reautorização, persistência, carregamento do player, recarga,
controle por conta e permissões de entidade.
