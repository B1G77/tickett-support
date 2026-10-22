# Apoio Tickett

Criei este sistema para o support@tickett.com. De 2 em 2 minutos lê a caixa de correio, arruma cada email numa pasta e escreve um rascunho de resposta com o Claude, a partir do tickett.com, dos Termos e do documento de eventos. A equipa aprova as respostas num painel web, e o administrador vê os números e controla o envio automático.

## O que está aqui

| Pasta | Conteúdo |
|---|---|
| `src/` | O router (`router.py`), o painel (`webapp.py`) e os módulos de apoio. Python 3.12+, só a biblioteca padrão mais o SDK da Anthropic. |
| `web/` | O painel em React + TypeScript (Vite). |
| `config/` | Definições (`router.json`) e as regras das respostas (`rulebook.md`). |
| `tests/` | Testes: `python3 tests/run.py` e `cd web && npm test`. |
| `deploy/` | Instalação no servidor (Linux com systemd). |
| `scripts/` | Instalação no Mac (`router-install.sh`) e ferramentas dos relatórios. |

## Instalar num servidor

Preciso de Linux com systemd, Python 3.12 ou mais recente, `git` e `python3-venv`, e Node 22 para compilar o painel.

```
sudo bash deploy/server-setup.sh suporte.example.com
```

Este passo cria os utilizadores, as pastas e os dois serviços (`tickett-router` e `tickett-webapp`). Depois preencham a password da caixa de correio e a chave da Anthropic em `/etc/tickett-router/secrets.env` e instalem:

```
sudo bash deploy/install.sh
```

O `install.sh` compila o painel, instala o commit atual, corre os testes e arranca os serviços. Se o router falhar nos primeiros 4 minutos, volta à versão anterior.

O painel escuta só em `127.0.0.1:8787`. Um proxy inverso com HTTPS põe-no online no endereço dado ao `server-setup.sh`. As contas do painel (`admin` e `agent`) recebem a palavra-passe com:

```
sudo -u tickett /opt/tickett-router/venv/bin/python /opt/tickett-router/current/src/webapp.py --set-password admin
```
