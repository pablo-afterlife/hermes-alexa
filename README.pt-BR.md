# hermes-alexa

A Alexa como canal nativo do [Hermes Agent](https://github.com/NousResearch/hermes-agent):
fale com o Hermes pelo Echo e deixe o Hermes falar de volta, **sem tirar a Alexa**.

> 🚧 Fase 2 de 4 (em teste): fale com o Hermes pelo Echo, com uma skill da Alexa só sua.
> 🇺🇸 [Read in English](README.md)

## O que já funciona

Cinco ferramentas, de qualquer canal do Hermes (Telegram, CLI, app, agendamentos…):

| Ferramenta | O que faz |
|---|---|
| `alexa_speak` | Fala algo no Echo (`announce: true` toca o sinal antes) |
| `alexa_command` | Dá um comando à Alexa como se fosse falado: "apaga a luz da sala", timer, música |
| `alexa_devices` | Lista os Echos da conta Amazon |
| `alexa_volume` | Ajusta o volume de um Echo |
| `alexa_routines` | Lista ou executa suas rotinas da Alexa |

E o comando `hermes alexa setup | login | status | start | stop | say "texto"`.

Testado com Echo Dot (5ª geração) e conta brasileira (amazon.com.br).

## Falar com o Hermes pelo Echo

"Alexa, pede pro Hermes Agent resumir meus e-mails" chega ao seu Hermes como uma mensagem do
Telegram. Resposta rápida sai na hora; se demorar, a Alexa diz "já te aviso" e anuncia no
mesmo Echo quando ficar pronto. "Alexa, abre o Hermes Agent" começa uma conversa de ida e volta.
Cada Echo é uma conversa. Agendamentos com `deliver=alexa` anunciam no seu Echo.

Precisa de uma skill da Alexa sua (grátis, fica em desenvolvimento, só na sua conta) e de um
túnel HTTPS até a sua máquina. A Fase 3 transforma isso num comando só; por enquanto:

1. Crie a skill a partir de [`skill/`](skill/) com o
   [ASK CLI](https://developer.amazon.com/docs/smapi/quick-start-alexa-skills-kit-command-line-interface.html):
   `ask smapi create-skill-for-vendor` com `skill/skill.json` (troque `{{ENDPOINT}}` pela sua
   URL HTTPS), depois `ask smapi set-interaction-model -l pt-BR` com
   `skill/interactionModels/custom/pt-BR.json` e `ask smapi set-skill-enablement -g development`.
2. Publique o endpoint: por exemplo `tailscale funnel --bg --set-path /alexa http://127.0.0.1:8796/alexa`.
3. Ponha `ALEXA_SKILL_ID=amzn1.ask.skill...` no `.env` do Hermes e reinicie o gateway.
4. Diga "Alexa, abre o Hermes Agent". Na primeira vez a Alexa lê um código de pareamento: aprove
   com `hermes pairing approve alexa <código>` (ou use `ALEXA_ALLOWED_USERS`).

Só entram pedidos assinados pela Amazon para o id da sua skill (ver a
[arquitetura](docs/architecture.md)). Voz não é senha: pela voz o Hermes só busca na web,
consulta conversas anteriores e usa as ferramentas da Alexa. Terminal, código e arquivos só
se você escolher em `platform_toolsets.alexa`.

Se a sessão da Amazon parar de funcionar, o Echo deixa de prometer "já te aviso" e diz para
rodar `hermes alexa login`. Para receber o aviso também no Telegram, use
`ALEXA_ALERT_TO=telegram` (vai para o `TELEGRAM_HOME_CHANNEL`) e permita que o plugin inicie
uma mensagem lá:

```yaml
plugins:
  entries:
    hermes-alexa:
      allow_gateway_injection: true
```

## Instalação

Precisa do [uv](https://docs.astral.sh/uv/) (vem com o Hermes no Windows) e de uma conta
Amazon com verificação em duas etapas por **app autenticador** (SMS não funciona).

```bash
git clone https://github.com/pablo-afterlife/hermes-alexa ~/.hermes/plugins/hermes-alexa
hermes plugins enable hermes-alexa
hermes alexa setup        # instala o bridge e pede o login da Amazon uma vez
hermes alexa say "Olá, aqui é o Hermes"
```

A senha nunca é guardada; só a sessão da Amazon, em
`<hermes home>/plugin-data/hermes-alexa/` (trate como uma senha).

Detalhes técnicos, roadmap e desenvolvimento: [README em inglês](README.md) e
[docs/architecture.md](docs/architecture.md).

## Licença

MIT
