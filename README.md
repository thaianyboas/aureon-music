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

## Motor nativo — prévia HACS 1.1.0b13

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

### Teste de reprodução em grupos Spotify Connect

A prévia 1.1.0b8 consulta novamente os dispositivos após a tentativa de transferência,
usa o identificador atualizado do alto-falante escolhido e tenta tocar a faixa diretamente
quando a playlist do histórico retorna 404. Testado com Spotify simulado em Home Assistant.
A reprodução física no grupo Wohnung Musik ainda precisa de validação.

A prévia 1.1.0b9 verifica o estado após HTTP 502, 503 ou 504 no comando de
reprodução e permite uma nova tentativa no mesmo destino. A resposta da API
do Spotify ao grupo físico Wohnung Musik continua pendente de validação.

A prévia 1.1.0b10 trata alto-falantes e grupos Amazon (Alexa) que recusam o início pela Web API do
Spotify: com a integração **Alexa Devices** do Home Assistant (ou **Alexa Media Player** do HACS), o AUREON
pede à Alexa que toque a música nesse alto-falante — num grupo, por outro Echo ("spiele … von Spotify auf
Wohnung Musik", no idioma do site Amazon) —, espera a sessão Spotify Connect ficar ativa e depois toca a faixa
exata dentro da playlist. Sem nenhuma das duas, a mensagem explica isso e mostra o código HTTP do Spotify.

A prévia 1.1.0b11: os alto-falantes Amazon obedecem a pausa, o play, o volume e as faixas, mas o Spotify
muitas vezes responde HTTP 502 depois de esperar por eles. O AUREON confere o estado depois do erro e só
mostra um erro quando o comando realmente não teve efeito. Os botões mudam na hora; se o alto-falante demora,
o comando termina em segundo plano. Um alto-falante ou grupo Amazon que não está ativo é acordado primeiro
pela Alexa, sem esperar pelas tentativas da Web API.

A prévia 1.1.0b12 retoma uma sessão ativa sem consultar antes o Spotify e espera no máximo 0,8 s pela resposta
de um alto-falante lento; o resto termina em segundo plano. Depois de cada comando o estado é lido de novo
aos 1, 2,5 e 5 s, e enquanto a música toca o cartão acompanha o Spotify a cada 5 s (antes 15 s).

A prévia 1.1.0b13 tem um alto-falante padrão por conta: no AUREON, em Configurações → Alto-falantes, toque em
"Tornar padrão". Play, uma música ou uma playlist vão para ele sempre que nada está tocando e nenhum outro
alto-falante foi escolhido (um grupo Amazon adormecido é acordado pela Alexa). O padrão fica nas opções da
integração (ação `aureon_music.native_write`, `set_default_speaker`, nome vazio remove). Play, pausa, volume,
aleatório e repetir mantêm o que foi tocado por até 8 s enquanto o Spotify ainda informa o estado antigo, para o
botão não voltar atrás.
