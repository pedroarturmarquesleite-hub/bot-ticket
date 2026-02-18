## Deploy na Hostinger VPS (Ubuntu)

1. Acesse o VPS por SSH:
   - `ssh root@SEU_IP`
2. Instale dependências:
   - `apt update && apt upgrade -y`
   - `apt install -y python3 python3-venv python3-pip git`
3. Clone o projeto:
   - `cd /opt`
   - `git clone https://github.com/pedroarturmarquesleite-hub/bot-ticket.git`
   - `cd bot-ticket`
4. Crie e ative venv:
   - `python3 -m venv .venv`
   - `source .venv/bin/activate`
   - `pip install -r requirements.txt`
5. Defina variáveis:
   - `DISCORD_TOKEN=...`
   - `APP_DATA_DIR=/opt/bot-ticket/data`
6. Crie o diretório de dados:
   - `mkdir -p /opt/bot-ticket/data`
7. Rode manualmente para teste:
   - `python main.py`

### Serviço systemd

Crie `/etc/systemd/system/bot-ticket.service`:

```ini
[Unit]
Description=Bot Ticket Discord
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/bot-ticket
Environment=DISCORD_TOKEN=SEU_TOKEN_AQUI
Environment=APP_DATA_DIR=/opt/bot-ticket/data
ExecStart=/opt/bot-ticket/.venv/bin/python /opt/bot-ticket/main.py
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
```

Ative o serviço:
- `systemctl daemon-reload`
- `systemctl enable bot-ticket`
- `systemctl start bot-ticket`
- `systemctl status bot-ticket`

Logs:
- `journalctl -u bot-ticket -f`
