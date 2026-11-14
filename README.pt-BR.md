# hermes-alexa

A Alexa como canal nativo do [Hermes Agent](https://github.com/NousResearch/hermes-agent):
fale com o Hermes pelo Echo e deixe o Hermes falar de volta, **sem tirar a Alexa**.

> 🚧 Fase 1 de 4: o Hermes já faz o seu Echo falar. Falar *com* o Hermes pela Alexa vem a seguir.
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
