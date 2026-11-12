# AUREON Música

SpotifyPlus and Spotcast inside AUREON, as one Home Assistant integration (`aureon_music`). The AUREON dashboard uses
it automatically; the original SpotifyPlus and Spotcast integrations are no longer needed.

## Instalar (passo a passo)

1. HACS → ⋮ → **Repositórios personalizados** → `https://github.com/thaianyboas/aureon-music`, tipo **Integração**.
2. Baixe **AUREON Música** e **reinicie** o Home Assistant.
3. Configurações → Dispositivos e serviços → **Adicionar integração** → **AUREON Música**.
4. No formulário **Conectar ao Spotify**, informe o Client ID e autorize sua conta na tela do Spotify.
5. Em **AUREON → Música → Configurações**, selecione sua conta. Use a lista de alto-falantes para escolher
   um dispositivo disponibilizado pelo Spotify Connect.

No app Spotify Developer, o *Redirect URI* continua `https://my.home-assistant.io/redirect/oauth`.

## Para quem mantém

`scripts/build_from_upstream.py <spotifyplus> <spotcast> custom_components/aureon_music` refaz as pastas `*_core`
a partir de uma nova versão dos projetos originais. Licenças e mudanças: `NOTICE.md`, `LICENSE`, `licenses/`.

## Motor nativo — prévia HACS 1.1.0b6

O formulário **Conectar ao Spotify** cria uma entrada nativa e usa a Spotify Web API com OAuth/PKCE.
As entradas SpotifyPlus/Spotcast continuam disponíveis durante a validação. A prévia está disponível pela branch
`master` para testar com uma conta Spotify real e dispositivos físicos.

1. Faça um backup do Home Assistant.
2. No HACS, abra **AUREON Música**. Atualize as informações do repositório e use **Baixar novamente**. Se o
   HACS oferecer uma seleção de versão, escolha `master`.
3. Reinicie o Home Assistant e adicione **AUREON Música** para abrir diretamente o formulário **Conectar ao Spotify**. Mantenha sua conta anterior durante o teste.
4. Se o menu de Música do AUREON Dashboard não oferecer a conta nativa, atualize também o **AUREON Dashboard**
   pelo HACS e recarregue a página. O bundle da `master` já inclui o suporte à conta nativa.

O mínimo do pacote atual é Home Assistant 2025.4.0. O Cast do HA 2025.1 a 2025.3 fixa uma versão do
PyChromecast abaixo da exigida pela biblioteca legada que ainda acompanha o pacote.

Use o **Client ID** do seu app Spotify Developer. Com My Home Assistant habilitado,
cadastre `https://my.home-assistant.io/redirect/oauth` no app. Sem My Home Assistant, cadastre o endereço HTTPS
exato do seu Home Assistant seguido de `/auth/external/callback`. Autorize a conta no Spotify quando o Home Assistant
abrir a página de login. O fluxo nativo não pede Client Secret.

- Em Development Mode, o proprietário do app precisa de Spotify Premium e as contas de teste precisam estar
  autorizadas no app. A reprodução e a transferência também dependem dos requisitos da Spotify Web API.
- Escolha a nova conta em **Música → Configurações** no Dashboard compilado com o suporte nativo. O motor só
  transfere para dispositivos controláveis retornados pela API Spotify Connect. Se uma Alexa, TV ou Chromecast não
  aparecer, abra o Spotify no dispositivo e consulte a lista novamente. A API não garante inicialização em
  dispositivos adormecidos ou compatibilidade com todos os modelos.
- O token fica em armazenamento privado do Home Assistant, fora da entrada de configuração. Uma queda de conexão
  não apaga o token. O Spotify pode revogar o acesso e, atualmente, exige reautorização após seis meses. Reautorize
  a mesma conta pela interface do Home Assistant.
- Durante a migração, mantenha a entrada anterior e selecione a nova apenas depois de conferir sua biblioteca,
  reprodução e alto-falantes. Para voltar ao fluxo anterior, selecione a conta legada no Dashboard. A criação
  nativa não altera as entradas antigas. Como são autorizações diferentes, o login inicial da entrada nativa é
  necessário e a seleção do alto-falante deve ser confirmada na nova conta.

Documentação do Spotify: [Spotify Connect](https://developer.spotify.com/documentation/web-api/reference/get-a-users-available-devices),
[modos de cota](https://developer.spotify.com/documentation/web-api/concepts/quota-modes) e
[expiração de refresh tokens](https://developer.spotify.com/blog/2026-06-18-refresh-token-expiration).
