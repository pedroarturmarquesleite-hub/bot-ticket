## Deploy no Northflank (com persistência)

1. Suba este projeto no GitHub.
2. No Northflank, crie um **Service** do tipo **Worker** (não HTTP).
3. Escolha deploy por repositório e use o `Dockerfile` deste projeto.
4. Em **Environment Variables**, configure:
   - `DISCORD_TOKEN` = token do bot
   - `APP_DATA_DIR` = `/data`
5. Em **Storage / Volumes**, adicione um volume persistente e monte em:
   - Mount path: `/data`
6. Deploy.

### O que fica persistido em `/data`
- `panel_config.json`
- `aceite_config.json`
- `taxa_config.json`
- `ticket_state.json`
- `logs_config.json`
- `pix_keys.json`
- pasta `logs/`

### Migração no primeiro deploy
- Se `/data` estiver vazio, o bot tenta copiar os JSON legados de `/app` automaticamente.
- Isso ajuda a não perder configuração na primeira subida.
