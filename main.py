import discord
from discord import app_commands
from payments.pix import (
    set_pix,
    gerar_qrcode_pix,
    gerar_payload_pix,
    get_pix_data,
    validar_chave_pix,
)
import re
import json
import os
import shutil
import logging
import asyncio
import sqlite3
import html
import io
from logging.handlers import RotatingFileHandler
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo



ticket_count_by_guild = {}
ticket_middleman = {}
ticket_parties = {}
ticket_trade_parties = {}
ticket_creator = {}
ticket_loading_msg = {}
ticket_type = {}
ticket_negociacao = {}
ticket_operation_locks = {}
APP_DATA_DIR = os.getenv("APP_DATA_DIR", os.getcwd())
os.makedirs(APP_DATA_DIR, exist_ok=True)
PANEL_CONFIG_FILE = os.path.join(APP_DATA_DIR, "panel_config.json")
ACEITE_CONFIG_FILE = os.path.join(APP_DATA_DIR, "aceite_config.json")
TAXA_CONFIG_FILE = os.path.join(APP_DATA_DIR, "taxa_config.json")
TICKET_STATE_FILE = os.path.join(APP_DATA_DIR, "ticket_state.json")
LOGS_CONFIG_FILE = os.path.join(APP_DATA_DIR, "logs_config.json")
ROLE_CONFIG_FILE = os.path.join(APP_DATA_DIR, "role_config.json")
MIDDLE_CATEGORY_CONFIG_FILE = os.path.join(APP_DATA_DIR, "middle_category_config.json")
LEVELS_CONFIG_FILE = os.path.join(APP_DATA_DIR, "levels_config.json")
SPENDING_CONFIG_FILE = os.path.join(APP_DATA_DIR, "spending_config.json")
MM_TAXA_METRICS_FILE = os.path.join(APP_DATA_DIR, "mm_taxa_metrics.json")
SETTINGS_DB_FILE = os.path.join(APP_DATA_DIR, "bot_settings.db")
LOGS_DIR = os.path.join(APP_DATA_DIR, "logs")
LOGS_FILE = os.path.join(LOGS_DIR, "bot.log")
PANEL_DEFAULT_IMAGE_URL = (
    "https://media.discordapp.net/attachments/1359946778480218176/"
    "1472704120228938007/content.png?ex=69938a17&is=69923897&"
    "hm=18c6ebe99a86ef08f81467c72a418216f65c1093b718ea04589f86ef927f9eb5&="
    "&format=webp&quality=lossless&width=1296&height=864"
)


TAXA_PADRAO = {
    "acima_700_percentual": 0.02,
    "acima_400_fixo": 10.0,
    "acima_100_fixo": 8.0,
    "acima_8_fixo": 5.0,
    "ate_8_fixo": 5.0
}
VALOR_MAXIMO_OPERACAO = 1_000_000.0
COOLDOWN_ABRIR_TICKET_SEGUNDOS = 15
COOLDOWN_CLIQUE_CRITICO_SEGUNDOS = 3
cooldown_user_actions = {}
PALETA_CORES = {
    "primario": "#FB8C00",
    "info": "#FF9800",
    "sucesso": "#EF6C00",
    "aviso": "#E65100",
    "erro": "#C62828",
    "destaque": "#F4511E"
}
ESTILO_BOTAO = {
    "primario": discord.ButtonStyle.primary,
    "sucesso": discord.ButtonStyle.green,
    "aviso": discord.ButtonStyle.secondary,
    "perigo": discord.ButtonStyle.red
}


def migrar_arquivo_legado(destino, legados):
    if os.path.exists(destino):
        return
    for legado in legados:
        if legado and os.path.exists(legado):
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            shutil.copy2(legado, destino)
            return


def migrar_dados_legados():
    cwd = os.getcwd()
    migrar_arquivo_legado(PANEL_CONFIG_FILE, [os.path.join(cwd, "panel_config.json"), "panel_config.json"])
    migrar_arquivo_legado(ACEITE_CONFIG_FILE, [os.path.join(cwd, "aceite_config.json"), "aceite_config.json"])
    migrar_arquivo_legado(TAXA_CONFIG_FILE, [os.path.join(cwd, "taxa_config.json"), "taxa_config.json"])
    migrar_arquivo_legado(TICKET_STATE_FILE, [os.path.join(cwd, "ticket_state.json"), "ticket_state.json"])
    migrar_arquivo_legado(LOGS_CONFIG_FILE, [os.path.join(cwd, "logs_config.json"), "logs_config.json"])
    migrar_arquivo_legado(ROLE_CONFIG_FILE, [os.path.join(cwd, "role_config.json"), "role_config.json"])
    migrar_arquivo_legado(LEVELS_CONFIG_FILE, [os.path.join(cwd, "levels_config.json"), "levels_config.json"])
    migrar_arquivo_legado(SPENDING_CONFIG_FILE, [os.path.join(cwd, "spending_config.json"), "spending_config.json"])
    migrar_arquivo_legado(MM_TAXA_METRICS_FILE, [os.path.join(cwd, "mm_taxa_metrics.json"), "mm_taxa_metrics.json"])


def setup_logger():
    os.makedirs(LOGS_DIR, exist_ok=True)
    logger = logging.getLogger("bot_middleman")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        "%Y-%m-%d %H:%M:%S"
    )

    file_handler = RotatingFileHandler(
        LOGS_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


logger = setup_logger()


async def em_cooldown(interaction: discord.Interaction, action: str, segundos: int) -> bool:
    now = time.monotonic()
    guild_id = interaction.guild.id if interaction.guild else 0
    key = (guild_id, interaction.user.id, action)
    expira_em = cooldown_user_actions.get(key, 0.0)

    if expira_em > now:
        restante = int(expira_em - now) + 1
        texto = f"Aguarde {restante}s para usar este botão novamente."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(texto, ephemeral=True)
            else:
                await interaction.response.send_message(texto, ephemeral=True, delete_after=5)
        except Exception:
            pass
        return True

    cooldown_user_actions[key] = now + segundos

    # Limpa entradas expiradas para evitar crescimento infinito.
    if len(cooldown_user_actions) > 5000:
        expiradas = [k for k, v in cooldown_user_actions.items() if v <= now]
        for k in expiradas:
            cooldown_user_actions.pop(k, None)

    return False


def get_ticket_lock(canal_id: int) -> asyncio.Lock:
    lock = ticket_operation_locks.get(canal_id)
    if lock is None:
        lock = asyncio.Lock()
        ticket_operation_locks[canal_id] = lock
    return lock


async def ticket_lock_or_wait_msg(interaction: discord.Interaction, canal_id: int) -> asyncio.Lock | None:
    lock = get_ticket_lock(canal_id)
    if not lock.locked():
        return lock

    texto = "Outra ação já está em processamento neste ticket. Aguarde alguns segundos."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(texto, ephemeral=True)
        else:
            await interaction.response.send_message(texto, ephemeral=True, delete_after=60)
    except Exception:
        pass
    return None


def cor_paleta(chave: str = "primario") -> discord.Color:
    hex_cor = PALETA_CORES.get(chave, PALETA_CORES["primario"])
    return discord.Color.from_str(hex_cor)


def embed_fluxo(descricao: str, titulo: str | None = None, cor: discord.Color | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=titulo,
        description=descricao,
        color=cor or cor_paleta("primario")
    )
    return embed


async def enviar_fluxo(
    canal: discord.abc.Messageable,
    descricao: str,
    *,
    titulo: str | None = None,
    cor: discord.Color | None = None,
    view: discord.ui.View | None = None,
    file: discord.File | None = None
):
    embed = embed_fluxo(descricao, titulo=titulo, cor=cor)
    kwargs = {"embed": embed}
    if view is not None:
        kwargs["view"] = view
    if file is not None:
        kwargs["file"] = file
    return await canal.send(**kwargs)


def _id_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _party_id(value):
    if value is None:
        return None
    if hasattr(value, "id"):
        return value.id
    return _id_int(value)


def carregar_estado_tickets():
    default = {
        "next_ticket_number_by_guild": {},
        "middleman": {},
        "parties": {},
        "trade_parties": {},
        "creators": {},
        "types": {}
    }

    if not os.path.exists(TICKET_STATE_FILE):
        return default

    try:
        with open(TICKET_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return default

    next_numbers = {}
    legacy_next_number = _id_int(data.get("next_ticket_number"))
    if legacy_next_number is not None and legacy_next_number >= 0:
        # Compatibilidade com estado antigo (contador global).
        next_numbers["_legacy_global"] = legacy_next_number

    for guild_id, number in data.get("next_ticket_number_by_guild", {}).items():
        guild_int = _id_int(guild_id)
        num_int = _id_int(number)
        if guild_int is not None and num_int is not None and num_int >= 0:
            next_numbers[guild_int] = num_int

    middleman = {}
    for canal_id, user_id in data.get("middleman", {}).items():
        canal_int = _id_int(canal_id)
        user_int = _id_int(user_id)
        if canal_int is not None and user_int is not None:
            middleman[canal_int] = user_int

    parties = {}
    for canal_id, info in data.get("parties", {}).items():
        canal_int = _id_int(canal_id)
        if canal_int is None or not isinstance(info, dict):
            continue
        comprador = _id_int(info.get("comprador"))
        vendedor = _id_int(info.get("vendedor"))
        if comprador is not None and vendedor is not None:
            parties[canal_int] = {
                "comprador": comprador,
                "vendedor": vendedor
            }

    trade_parties = {}
    for canal_id, info in data.get("trade_parties", {}).items():
        canal_int = _id_int(canal_id)
        if canal_int is None or not isinstance(info, dict):
            continue
        pessoa1 = _id_int(info.get("pessoa1"))
        pessoa2 = _id_int(info.get("pessoa2"))
        if pessoa1 is not None and pessoa2 is not None:
            trade_parties[canal_int] = {
                "pessoa1": pessoa1,
                "pessoa2": pessoa2
            }

    creators = {}
    for canal_id, user_id in data.get("creators", {}).items():
        canal_int = _id_int(canal_id)
        user_int = _id_int(user_id)
        if canal_int is not None and user_int is not None:
            creators[canal_int] = user_int

    types = {}
    for canal_id, kind in data.get("types", {}).items():
        canal_int = _id_int(canal_id)
        if canal_int is None:
            continue
        if kind in {"pix", "brainrot", "trade"}:
            types[canal_int] = kind

    return {
        "next_ticket_number_by_guild": next_numbers,
        "middleman": middleman,
        "parties": parties,
        "trade_parties": trade_parties,
        "creators": creators,
        "types": types
    }


def salvar_estado_tickets():
    counters_payload = {}
    for guild_id, number in ticket_count_by_guild.items():
        if isinstance(guild_id, int) and isinstance(number, int) and number >= 0:
            counters_payload[str(guild_id)] = number

    payload = {
        "next_ticket_number_by_guild": counters_payload,
        "middleman": {str(k): int(v) for k, v in ticket_middleman.items()},
        "parties": {
            str(canal_id): {
                "comprador": _party_id(info.get("comprador")),
                "vendedor": _party_id(info.get("vendedor"))
            }
            for canal_id, info in ticket_parties.items()
            if isinstance(info, dict)
        },
        "trade_parties": {
            str(canal_id): {
                "pessoa1": _party_id(info.get("pessoa1")),
                "pessoa2": _party_id(info.get("pessoa2"))
            }
            for canal_id, info in ticket_trade_parties.items()
            if isinstance(info, dict)
        },
        "creators": {str(k): int(v) for k, v in ticket_creator.items()},
        "types": {str(k): v for k, v in ticket_type.items() if v in {"pix", "brainrot", "trade"}}
    }

    temp_file = f"{TICKET_STATE_FILE}.tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=4)
        os.replace(temp_file, TICKET_STATE_FILE)
    except OSError as e:
        print(f"Falha ao salvar estado de tickets: {e}")


def carregar_estado_tickets_memoria():
    global ticket_count_by_guild, ticket_middleman, ticket_parties, ticket_trade_parties, ticket_creator, ticket_type, ticket_loading_msg

    data = carregar_estado_tickets()
    ticket_count_by_guild = data["next_ticket_number_by_guild"]
    ticket_middleman = data["middleman"]
    ticket_parties = data["parties"]
    ticket_trade_parties = data["trade_parties"]
    ticket_creator = data["creators"]
    ticket_type = data["types"]
    ticket_loading_msg = {}


def salvar_tipo_ticket(canal_id, kind):
    ticket_type[canal_id] = kind
    salvar_estado_tickets()


def salvar_middleman_ticket(canal_id, middleman_id):
    ticket_middleman[canal_id] = middleman_id
    salvar_estado_tickets()


def salvar_partes_ticket(canal_id, comprador, vendedor):
    ticket_parties[canal_id] = {
        "comprador": comprador,
        "vendedor": vendedor
    }
    salvar_estado_tickets()


def salvar_partes_trade(canal_id, pessoa1, pessoa2):
    ticket_trade_parties[canal_id] = {
        "pessoa1": pessoa1,
        "pessoa2": pessoa2
    }
    salvar_estado_tickets()


def salvar_criador_ticket(canal_id, criador_id):
    cid = _id_int(canal_id)
    uid = _id_int(criador_id)
    if cid is None or uid is None:
        return
    ticket_creator[cid] = uid
    salvar_estado_tickets()


def remover_estado_ticket(canal_id):
    ticket_middleman.pop(canal_id, None)
    ticket_parties.pop(canal_id, None)
    ticket_trade_parties.pop(canal_id, None)
    ticket_creator.pop(canal_id, None)
    ticket_loading_msg.pop(canal_id, None)
    ticket_type.pop(canal_id, None)
    ticket_negociacao.pop(canal_id, None)
    ticket_operation_locks.pop(canal_id, None)
    salvar_estado_tickets()


def resolver_membro(guild, member_or_id):
    if member_or_id is None:
        return None
    if hasattr(member_or_id, "id"):
        return member_or_id
    member_id = _id_int(member_or_id)
    if member_id is None:
        return None
    return guild.get_member(member_id)


def obter_partes_ticket(canal):
    parties = ticket_parties.get(canal.id)
    if not isinstance(parties, dict):
        return None

    comprador = resolver_membro(canal.guild, parties.get("comprador"))
    vendedor = resolver_membro(canal.guild, parties.get("vendedor"))

    if comprador is None or vendedor is None:
        return None

    return {
        "comprador": comprador,
        "vendedor": vendedor
    }


def obter_partes_trade(canal):
    parties = ticket_trade_parties.get(canal.id)
    if not isinstance(parties, dict):
        return None

    pessoa1 = resolver_membro(canal.guild, parties.get("pessoa1"))
    pessoa2 = resolver_membro(canal.guild, parties.get("pessoa2"))

    if pessoa1 is None or pessoa2 is None:
        return None

    return {"pessoa1": pessoa1, "pessoa2": pessoa2}


def _formatar_mencao_usuario(user_id):
    uid = _id_int(user_id)
    if uid is None:
        return "Nao definido"
    return f"<@{uid}> (`{uid}`)"


async def gerar_transcricao_ticket(canal: discord.TextChannel):
    guild_icon = canal.guild.icon.url if canal.guild.icon else ""
    linhas_html = [
        "<!DOCTYPE html>",
        "<html lang='pt-BR'>",
        "<head>",
        "<meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>Transcrição - {html.escape(canal.name)}</title>",
        "<style>",
        "body{margin:0;background:#313338;color:#dbdee1;font-family:'Segoe UI',Tahoma,Geneva,Verdana,sans-serif;}",
        ".wrap{max-width:980px;margin:0 auto;padding:18px;}",
        ".header{display:flex;gap:14px;align-items:center;padding:8px 0 18px;border-bottom:1px solid #232428;}",
        ".server-icon{width:72px;height:72px;border-radius:50%;object-fit:cover;background:#1e1f22;}",
        ".server-title{font-size:38px;font-weight:800;line-height:1.05;color:#f2f3f5;}",
        ".server-sub{font-size:30px;font-weight:700;color:#c7ccd1;}",
        ".meta{font-size:28px;color:#b5bac1;margin-top:2px;}",
        ".msg{display:flex;gap:12px;padding:14px 0;border-bottom:1px solid #2a2c31;}",
        ".avatar{width:40px;height:40px;border-radius:50%;object-fit:cover;background:#1e1f22;flex:0 0 40px;}",
        ".content{flex:1;min-width:0;}",
        ".top{display:flex;gap:8px;align-items:center;flex-wrap:wrap;}",
        ".name{font-weight:700;color:#00a8fc;font-size:17px;}",
        ".badge{background:#5865f2;color:#fff;border-radius:5px;padding:1px 6px;font-size:11px;font-weight:700;}",
        ".time{color:#949ba4;font-size:12px;}",
        ".text{margin-top:4px;white-space:pre-wrap;word-break:break-word;line-height:1.35;}",
        ".emb{margin-top:8px;border-left:4px solid #ff5a5f;background:#2b2d31;border-radius:6px;padding:10px 12px;}",
        ".emb-title{font-weight:700;color:#fff;margin-bottom:6px;}",
        ".emb-field{margin-top:6px;}",
        ".emb-field b{color:#fff;}",
        ".att{margin-top:8px;}",
        ".att a{color:#00a8fc;text-decoration:none;}",
        ".att img{max-width:360px;max-height:260px;border-radius:6px;border:1px solid #232428;display:block;margin-top:6px;}",
        "</style>",
        "</head><body>",
        "<div class='wrap'>",
        "<div class='header'>",
        f"<img class='server-icon' src='{html.escape(guild_icon)}' alt='icon'>",
        "<div>",
        f"<div class='server-title'>{html.escape(canal.guild.name)}</div>",
        f"<div class='server-sub'>#{html.escape(canal.name)}</div>",
        f"<div class='meta'>{html.escape(str(canal.id))}</div>",
        "</div></div>",
    ]

    qtd_msgs = 0
    async for msg in canal.history(limit=None, oldest_first=True):
        qtd_msgs += 1
        ts = msg.created_at.astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y %H:%M:%S (BRT)")
        autor_nome = html.escape(msg.author.display_name)
        avatar = html.escape(msg.author.display_avatar.url)
        conteudo = html.escape(msg.content or "").replace("\n", "<br>")
        badge = "<span class='badge'>BOT</span>" if msg.author.bot else ""

        linhas_html.extend(
            [
                "<div class='msg'>",
                f"<img class='avatar' src='{avatar}' alt='avatar'>",
                "<div class='content'>",
                f"<div class='top'><span class='name'>{autor_nome}</span>{badge}<span class='time'>{html.escape(ts)}</span></div>",
            ]
        )

        if conteudo:
            linhas_html.append(f"<div class='text'>{conteudo}</div>")

        for emb in msg.embeds:
            linhas_html.append("<div class='emb'>")
            if emb.title:
                linhas_html.append(f"<div class='emb-title'>{html.escape(str(emb.title))}</div>")
            if emb.description:
                emb_desc = html.escape(str(emb.description)).replace("\n", "<br>")
                linhas_html.append(f"<div class='text'>{emb_desc}</div>")
            for field in emb.fields:
                fname = html.escape(str(field.name))
                fvalue = html.escape(str(field.value)).replace("\n", "<br>")
                linhas_html.append(f"<div class='emb-field'><b>{fname}</b><br>{fvalue}</div>")
            if emb.image and emb.image.url:
                linhas_html.append(f"<div class='att'><img src='{html.escape(emb.image.url)}' alt='embed-image'></div>")
            linhas_html.append("</div>")

        if msg.attachments:
            linhas_html.append("<div class='att'>")
            for a in msg.attachments:
                url = html.escape(a.url)
                nome = html.escape(a.filename)
                linhas_html.append(f"<a href='{url}' target='_blank' rel='noopener'>{nome}</a>")
                if a.content_type and a.content_type.startswith("image/"):
                    linhas_html.append(f"<img src='{url}' alt='{nome}'>")
            linhas_html.append("</div>")

        linhas_html.append("</div></div>")

    linhas_html.append("</div></body></html>")
    html_bytes = "\n".join(linhas_html).encode("utf-8", errors="replace")
    return html_bytes, qtd_msgs


async def enviar_log_fechamento_ticket(guild, canal):
    if guild is None or canal is None:
        return

    canal_id = canal.id

    middle_id = ticket_middleman.get(canal_id)
    partes = ticket_parties.get(canal_id, {})
    partes_trade = ticket_trade_parties.get(canal_id, {})
    dados_negociacao = ticket_negociacao.get(canal_id, {}) if isinstance(ticket_negociacao.get(canal_id), dict) else {}
    tipo = ticket_type.get(canal_id, "desconhecido")

    valor_brainrot_txt = "Não informado"
    valor_taxa_txt = "Não informado"
    valor_total_txt = "Não informado"
    valor_negociado_num = None
    valor_taxa_num = None

    if isinstance(dados_negociacao, dict):
        valor_negociado = dados_negociacao.get("valor")
        try:
            valor_negociado = float(valor_negociado)
        except (TypeError, ValueError):
            valor_negociado = None

        if valor_negociado is not None:
            valor_negociado_num = valor_negociado
            valor_brainrot_txt = f"R$ {valor_negociado:.2f}"
            if tipo == "brainrot":
                valor_taxa_txt = "R$ 0.00 (taxa em item)"
                valor_taxa_num = 0.0
            else:
                valor_taxa_num = float(calcular_taxa(valor_negociado, guild.id))
                valor_taxa_txt = f"R$ {valor_taxa_num:.2f}"

    if middle_id is not None and valor_taxa_num is not None and valor_taxa_num > 0:
        try:
            registrar_mm_taxa(guild.id, middle_id, valor_taxa_num)
        except Exception:
            logger.exception(
                "Falha ao registrar taxa de middle guild_id=%s canal_id=%s middle_id=%s taxa=%s",
                guild.id,
                canal_id,
                middle_id,
                valor_taxa_num
            )

    if valor_negociado_num is not None and valor_taxa_num is not None:
        valor_total_txt = f"R$ {valor_negociado_num + valor_taxa_num:.2f}"
    valor_total_num = (valor_negociado_num + valor_taxa_num) if (valor_negociado_num is not None and valor_taxa_num is not None) else None

    ids_participantes = set()
    if isinstance(partes, dict):
        comprador_id = _party_id(partes.get("comprador"))
        vendedor_id = _party_id(partes.get("vendedor"))
        if comprador_id is not None:
            ids_participantes.add(comprador_id)
        if vendedor_id is not None:
            ids_participantes.add(vendedor_id)
    if isinstance(partes_trade, dict):
        pessoa1_id = _party_id(partes_trade.get("pessoa1"))
        pessoa2_id = _party_id(partes_trade.get("pessoa2"))
        if pessoa1_id is not None:
            ids_participantes.add(pessoa1_id)
        if pessoa2_id is not None:
            ids_participantes.add(pessoa2_id)

    participantes_txt = "Nenhum participante registrado."
    if ids_participantes:
        participantes_txt = "\n".join(
            f"- Adicionado: {_formatar_mencao_usuario(uid)}"
            for uid in sorted(ids_participantes)
        )

    totais_gastos_participantes = {}
    if valor_total_num is not None and ids_participantes:
        for uid in ids_participantes:
            try:
                novo_total = adicionar_gasto_usuario(guild.id, uid, valor_total_num)
                totais_gastos_participantes[uid] = novo_total
                await atualizar_cargos_niveis_usuario(guild, uid, novo_total)
            except Exception:
                logger.exception(
                    "Falha ao processar niveis por gasto guild_id=%s canal_id=%s user_id=%s valor=%s",
                    guild.id,
                    canal_id,
                    uid,
                    valor_total_num
                )
    else:
        for uid in ids_participantes:
            totais_gastos_participantes[uid] = obter_gasto_usuario(guild.id, uid)

    mensagem = (
        f"Ticket fechado: {canal.name} (`{canal.id}`)\n"
        f"Tipo: {tipo}\n"
        f"Middle: {_formatar_mencao_usuario(middle_id)}\n"
        f"Valor total (valor + taxa): {valor_total_txt}\n"
        f"Valor do Brainrot negociado: {valor_brainrot_txt}\n"
        f"Valor da taxa: {valor_taxa_txt}\n"
        f"Participantes:\n{participantes_txt}"
    )
    logger.info(mensagem)

    logs_channel_id = _id_int(get_logs_canal_id(guild.id))
    canal_logs = None
    if logs_channel_id is not None:
        canal_logs = guild.get_channel(logs_channel_id)
        if canal_logs is None:
            try:
                canal_logs = await guild.fetch_channel(logs_channel_id)
            except Exception:
                logger.warning(
                    "Nao foi possivel encontrar canal de logs guild_id=%s channel_id=%s",
                    guild.id,
                    logs_channel_id
                )
                canal_logs = None

        if canal_logs is not None and not isinstance(canal_logs, discord.TextChannel):
            logger.warning(
                "Canal configurado de logs nao eh TextChannel guild_id=%s channel_id=%s",
                guild.id,
                logs_channel_id
            )
            canal_logs = None

    tipo_base = "Troca venda/compra"
    cor = cor_paleta("primario")
    if tipo == "brainrot":
        tipo_base = "Troca de Brainrot"
        cor = cor_paleta("aviso")
    elif tipo == "trade":
        tipo_base = "Trade"
        cor = cor_paleta("destaque")

    participantes_resumo = []
    for uid in sorted(ids_participantes):
        participantes_resumo.append(f"<@{uid}>")

    participantes_resumo_txt = " ".join(participantes_resumo) if participantes_resumo else "Não informado"

    valor_resumo = valor_total_txt

    horario_brasilia = discord.utils.utcnow().astimezone(ZoneInfo("America/Sao_Paulo"))
    horario_txt = horario_brasilia.strftime("%d/%m/%Y %H:%M (BRT)")

    numero_ticket = None
    m_ticket = re.search(r"(\d+)$", canal.name or "")
    if m_ticket:
        numero_ticket = m_ticket.group(1)

    titulo_log = f"✅ Intermediação #{numero_ticket}" if numero_ticket else "✅ Intermediação"
    prova_txt = f"Proof #{numero_ticket}" if numero_ticket else f"Ticket ID: {canal.id}"
    middle_txt = _formatar_mencao_usuario(middle_id)

    embed = discord.Embed(
        title=titulo_log,
        description=(
            "• **Nova intermediação concluída com sucesso!**\n"
            f"{prova_txt}\n\n"
            f"• **Valor:** {valor_resumo}\n"
            f"• **Participantes:** {participantes_resumo_txt}\n"
            f"• **Middle Man:** {middle_txt}\n"
            f"🗓️ {horario_txt}"
        ),
        color=cor_paleta("primario")
    )
    embed.set_thumbnail(url="https://media.discordapp.net/attachments/1473531494432641034/1484349942901379124/image.png?ex=69bde81c&is=69bc969c&hm=45aecc9cefb2d3080c502b6d3d19d19d7f7bd5aa07c6077689bb2bb9ef78376b&=&format=webp&quality=lossless&width=975&height=975")
    embed.set_footer(text="Automático")
    if canal_logs is not None:
        try:
            await canal_logs.send(embed=embed)
        except discord.Forbidden:
            logger.warning(
                "Sem permissao para enviar logs no canal guild_id=%s channel_id=%s",
                guild.id,
                logs_channel_id
            )
        except Exception:
            logger.exception(
                "Falha ao enviar log no Discord guild_id=%s channel_id=%s",
                guild.id,
                logs_channel_id
            )

    admin_logs_channel_id = _id_int(get_log_admin_canal_id(guild.id))
    if admin_logs_channel_id is not None:
        canal_admin_logs = guild.get_channel(admin_logs_channel_id)
        if canal_admin_logs is None:
            try:
                canal_admin_logs = await guild.fetch_channel(admin_logs_channel_id)
            except Exception:
                canal_admin_logs = None

        if isinstance(canal_admin_logs, discord.TextChannel):
            try:
                html_bytes, qtd_msgs = await gerar_transcricao_ticket(canal)
                arq_html = discord.File(io.BytesIO(html_bytes), filename=f"transcricao-{canal.id}.html")
                await canal_admin_logs.send(
                    embed=embed_fluxo(
                        f"Transcrição gerada para `{canal.name}`.\n"
                        f"Mensagens: **{qtd_msgs}**",
                        titulo="🧾 Transcrição do Ticket",
                        cor=cor_paleta("info")
                    ),
                    file=arq_html
                )
            except discord.Forbidden:
                logger.warning(
                    "Sem permissao para enviar transcricao no canal admin guild_id=%s channel_id=%s",
                    guild.id,
                    admin_logs_channel_id
                )
            except Exception:
                logger.exception(
                    "Falha ao enviar transcricao no canal admin guild_id=%s channel_id=%s canal_id=%s",
                    guild.id,
                    admin_logs_channel_id,
                    canal.id
                )

    for uid in sorted(ids_participantes):
        membro = guild.get_member(uid)
        if membro is None:
            try:
                membro = await guild.fetch_member(uid)
            except Exception:
                continue

        total_gasto_usuario = totais_gastos_participantes.get(uid, obter_gasto_usuario(guild.id, uid))
        embed_pv = discord.Embed(
            title=titulo_log,
            description=(
                "Seu ticket foi finalizado com sucesso.\n\n"
                f"• **Tipo:** {tipo_base}\n"
                f"• **Valor do ticket:** {valor_resumo}\n"
                f"• **Total gasto no servidor:** R$ {total_gasto_usuario:.2f}\n"
                f"• **Horário:** {horario_txt}"
            ),
            color=cor_paleta("primario")
        )
        embed_pv.set_footer(text=f"Servidor: {guild.name}")

        try:
            await membro.send(embed=embed_pv)
        except discord.Forbidden:
            logger.info(
                "PV bloqueado para envio de log de ticket guild_id=%s user_id=%s canal_id=%s",
                guild.id,
                uid,
                canal.id
            )
        except Exception:
            logger.exception(
                "Falha ao enviar log no PV guild_id=%s user_id=%s canal_id=%s",
                guild.id,
                uid,
                canal.id
            )


def _load_json_file(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def init_settings_db():
    with sqlite3.connect(SETTINGS_DB_FILE) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY,
                painel_channel_id INTEGER,
                painel_image_url TEXT,
                aceite_channel_id INTEGER,
                logs_channel_id INTEGER,
                log_admin_channel_id INTEGER,
                middle_role_id INTEGER,
                middle_category_id INTEGER
            )
            """
        )
        # Compatibilidade com bancos criados antes do campo log_admin_channel_id.
        cols = [row[1] for row in conn.execute("PRAGMA table_info(guild_settings)").fetchall()]
        if "log_admin_channel_id" not in cols:
            conn.execute("ALTER TABLE guild_settings ADD COLUMN log_admin_channel_id INTEGER")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_taxa (
                guild_id INTEGER NOT NULL,
                faixa TEXT NOT NULL,
                valor REAL NOT NULL,
                PRIMARY KEY (guild_id, faixa)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_levels (
                guild_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                min_total REAL NOT NULL,
                PRIMARY KEY (guild_id, role_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS migration_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        conn.commit()


def migrate_json_configs_to_db():
    with sqlite3.connect(SETTINGS_DB_FILE) as conn:
        row = conn.execute(
            "SELECT value FROM migration_meta WHERE key = 'json_to_sqlite_v1'"
        ).fetchone()
        if row and str(row[0]) == "done":
            return

        painel = _load_json_file(PANEL_CONFIG_FILE)
        aceite = _load_json_file(ACEITE_CONFIG_FILE)
        logs_cfg = _load_json_file(LOGS_CONFIG_FILE)
        role_cfg = _load_json_file(ROLE_CONFIG_FILE)
        category_cfg = _load_json_file(MIDDLE_CATEGORY_CONFIG_FILE)
        taxa_cfg = _load_json_file(TAXA_CONFIG_FILE)
        levels_cfg = _load_json_file(LEVELS_CONFIG_FILE)

        guild_ids = set()
        guild_ids.update(_id_int(k) for k in painel.keys())
        guild_ids.update(_id_int(k) for k in aceite.keys())
        guild_ids.update(_id_int(k) for k in logs_cfg.keys())
        guild_ids.update(_id_int(k) for k in role_cfg.keys())
        guild_ids.update(_id_int(k) for k in category_cfg.keys())
        guild_ids.update(_id_int(k) for k in levels_cfg.keys())
        guild_ids.update(_id_int(k) for k in taxa_cfg.keys())
        guild_ids.discard(None)

        for guild_id in guild_ids:
            entry = painel.get(str(guild_id))
            if isinstance(entry, dict):
                painel_channel_id = _id_int(entry.get("channel_id"))
                painel_image_url = entry.get("image_url")
            else:
                painel_channel_id = _id_int(entry)
                painel_image_url = None
            conn.execute(
                """
                INSERT INTO guild_settings (
                    guild_id, painel_channel_id, painel_image_url, aceite_channel_id, logs_channel_id, middle_role_id, middle_category_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    painel_channel_id=excluded.painel_channel_id,
                    painel_image_url=excluded.painel_image_url,
                    aceite_channel_id=excluded.aceite_channel_id,
                    logs_channel_id=excluded.logs_channel_id,
                    middle_role_id=excluded.middle_role_id,
                    middle_category_id=excluded.middle_category_id
                """,
                (
                    guild_id,
                    painel_channel_id,
                    painel_image_url,
                    _id_int(aceite.get(str(guild_id))),
                    _id_int(logs_cfg.get(str(guild_id))),
                    _id_int(role_cfg.get(str(guild_id))),
                    _id_int(category_cfg.get(str(guild_id))),
                ),
            )

        # Migra níveis por servidor.
        for guild_key, lista in levels_cfg.items():
            guild_id = _id_int(guild_key)
            if guild_id is None or not isinstance(lista, list):
                continue
            for item in lista:
                if not isinstance(item, dict):
                    continue
                role_id = _id_int(item.get("role_id"))
                try:
                    min_total = float(item.get("min_total"))
                except (TypeError, ValueError):
                    continue
                if role_id is None or min_total < 0:
                    continue
                conn.execute(
                    """
                    INSERT INTO guild_levels (guild_id, role_id, min_total)
                    VALUES (?, ?, ?)
                    ON CONFLICT(guild_id, role_id) DO UPDATE SET min_total=excluded.min_total
                    """,
                    (guild_id, role_id, min_total),
                )

        # Migra taxa por servidor (ou legado global para guild_id=0).
        if isinstance(taxa_cfg, dict):
            if any(k in taxa_cfg for k in TAXA_PADRAO.keys()):
                for faixa, valor in _normalizar_taxa_config(taxa_cfg).items():
                    conn.execute(
                        """
                        INSERT INTO guild_taxa (guild_id, faixa, valor)
                        VALUES (0, ?, ?)
                        ON CONFLICT(guild_id, faixa) DO UPDATE SET valor=excluded.valor
                        """,
                        (faixa, float(valor)),
                    )
            else:
                for guild_key, cfg in taxa_cfg.items():
                    guild_id = _id_int(guild_key)
                    if guild_id is None:
                        continue
                    cfg_norm = _normalizar_taxa_config(cfg if isinstance(cfg, dict) else {})
                    for faixa, valor in cfg_norm.items():
                        conn.execute(
                            """
                            INSERT INTO guild_taxa (guild_id, faixa, valor)
                            VALUES (?, ?, ?)
                            ON CONFLICT(guild_id, faixa) DO UPDATE SET valor=excluded.valor
                            """,
                            (guild_id, faixa, float(valor)),
                        )

        conn.execute(
            """
            INSERT INTO migration_meta (key, value)
            VALUES ('json_to_sqlite_v1', 'done')
            ON CONFLICT(key) DO UPDATE SET value='done'
            """
        )
        conn.commit()


def carregar_painel_config():
    with sqlite3.connect(SETTINGS_DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT guild_id, painel_channel_id, painel_image_url
            FROM guild_settings
            WHERE painel_channel_id IS NOT NULL OR painel_image_url IS NOT NULL
            """
        ).fetchall()
    data = {}
    for row in rows:
        data[str(row["guild_id"])] = {
            "channel_id": _id_int(row["painel_channel_id"]),
            "image_url": row["painel_image_url"]
        }
    return data


def salvar_painel_config(data):
    if not isinstance(data, dict):
        return
    with sqlite3.connect(SETTINGS_DB_FILE) as conn:
        for guild_key, entry in data.items():
            guild_id = _id_int(guild_key)
            if guild_id is None:
                continue
            if isinstance(entry, dict):
                painel_id = _id_int(entry.get("channel_id"))
                image_url = entry.get("image_url")
            else:
                painel_id = _id_int(entry)
                image_url = None
            conn.execute(
                """
                INSERT INTO guild_settings (guild_id, painel_channel_id, painel_image_url)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    painel_channel_id=excluded.painel_channel_id,
                    painel_image_url=COALESCE(excluded.painel_image_url, guild_settings.painel_image_url)
                """,
                (guild_id, painel_id, image_url)
            )
        conn.commit()


def _set_guild_setting(guild_id, column, value):
    if _id_int(guild_id) is None:
        return
    if column not in {
        "painel_channel_id",
        "painel_image_url",
        "aceite_channel_id",
        "logs_channel_id",
        "log_admin_channel_id",
        "middle_role_id",
        "middle_category_id",
    }:
        return
    with sqlite3.connect(SETTINGS_DB_FILE) as conn:
        conn.execute(
            f"""
            INSERT INTO guild_settings (guild_id, {column})
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                {column}=excluded.{column}
            """,
            (int(guild_id), value)
        )
        conn.commit()


def _get_guild_setting(guild_id, column):
    if _id_int(guild_id) is None:
        return None
    with sqlite3.connect(SETTINGS_DB_FILE) as conn:
        row = conn.execute(
            f"SELECT {column} FROM guild_settings WHERE guild_id = ?",
            (int(guild_id),)
        ).fetchone()
    if not row:
        return None
    return row[0]


def set_painel_canal(guild_id, channel_id):
    _set_guild_setting(guild_id, "painel_channel_id", _id_int(channel_id))


def get_painel_canal_id(guild_id):
    return _id_int(_get_guild_setting(guild_id, "painel_channel_id"))


def set_painel_image_url(guild_id, image_url):
    _set_guild_setting(guild_id, "painel_image_url", image_url)


def get_painel_image_url(guild_id):
    url = _get_guild_setting(guild_id, "painel_image_url")
    if isinstance(url, str) and url.strip():
        return url.strip()
    return None


def validar_url_imagem(url):
    if not isinstance(url, str):
        return False
    url = url.strip()
    if not url:
        return False
    return url.startswith("http://") or url.startswith("https://")


def set_aceite_canal(guild_id, channel_id):
    _set_guild_setting(guild_id, "aceite_channel_id", _id_int(channel_id))


def get_aceite_canal_id(guild_id):
    return _id_int(_get_guild_setting(guild_id, "aceite_channel_id"))


def set_logs_canal(guild_id, channel_id):
    _set_guild_setting(guild_id, "logs_channel_id", _id_int(channel_id))


def get_logs_canal_id(guild_id):
    return _id_int(_get_guild_setting(guild_id, "logs_channel_id"))


def set_log_admin_canal(guild_id, channel_id):
    _set_guild_setting(guild_id, "log_admin_channel_id", _id_int(channel_id))


def get_log_admin_canal_id(guild_id):
    return _id_int(_get_guild_setting(guild_id, "log_admin_channel_id"))


def set_middle_role_id(guild_id, role_id):
    _set_guild_setting(guild_id, "middle_role_id", _id_int(role_id))


def get_middle_role_id(guild_id):
    return _id_int(_get_guild_setting(guild_id, "middle_role_id"))


def get_middle_role(guild):
    if guild is None:
        return None
    role_id = get_middle_role_id(guild.id)
    if role_id is not None:
        role = guild.get_role(role_id)
        if role is not None:
            return role
    # fallback para compatibilidade com configuração antiga
    return discord.utils.get(guild.roles, name="Middle Man")


def set_middle_category_id(guild_id, category_id):
    _set_guild_setting(guild_id, "middle_category_id", _id_int(category_id))


def get_middle_category_id(guild_id):
    return _id_int(_get_guild_setting(guild_id, "middle_category_id"))


def get_levels_guild(guild_id):
    raw = []
    with sqlite3.connect(SETTINGS_DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT role_id, min_total FROM guild_levels WHERE guild_id = ? ORDER BY min_total ASC",
            (int(guild_id),)
        ).fetchall()
    for row in rows:
        raw.append({"role_id": int(row["role_id"]), "min_total": float(row["min_total"])})
    niveis = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role_id = _id_int(item.get("role_id"))
        min_total = item.get("min_total")
        try:
            min_total = float(min_total)
        except (TypeError, ValueError):
            continue
        if role_id is None or min_total < 0:
            continue
        niveis.append({"role_id": role_id, "min_total": min_total})
    niveis.sort(key=lambda x: x["min_total"])
    return niveis


def set_level_guild(guild_id, role_id, min_total):
    with sqlite3.connect(SETTINGS_DB_FILE) as conn:
        conn.execute(
            """
            INSERT INTO guild_levels (guild_id, role_id, min_total)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, role_id) DO UPDATE SET min_total=excluded.min_total
            """,
            (int(guild_id), int(role_id), float(min_total))
        )
        conn.commit()


def carregar_spending_config():
    if not os.path.exists(SPENDING_CONFIG_FILE):
        return {}
    with open(SPENDING_CONFIG_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data
    return {}


def salvar_spending_config(data):
    with open(SPENDING_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def adicionar_gasto_usuario(guild_id, user_id, valor):
    data = carregar_spending_config()
    guild_key = str(guild_id)
    user_key = str(user_id)
    guild_data = data.get(guild_key, {})
    if not isinstance(guild_data, dict):
        guild_data = {}

    atual = guild_data.get(user_key, 0.0)
    try:
        atual = float(atual)
    except (TypeError, ValueError):
        atual = 0.0

    novo_total = atual + float(valor)
    guild_data[user_key] = round(novo_total, 2)
    data[guild_key] = guild_data
    salvar_spending_config(data)
    return float(guild_data[user_key])


def obter_gasto_usuario(guild_id, user_id):
    data = carregar_spending_config()
    guild_data = data.get(str(guild_id), {})
    if not isinstance(guild_data, dict):
        return 0.0
    valor = guild_data.get(str(user_id), 0.0)
    try:
        return float(valor)
    except (TypeError, ValueError):
        return 0.0


async def atualizar_cargos_niveis_usuario(guild, user_id, total_gasto):
    if guild is None:
        return
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except Exception:
            return

    niveis = get_levels_guild(guild.id)
    if not niveis:
        return

    for nivel in niveis:
        role = guild.get_role(nivel["role_id"])
        if role is None:
            continue
        deve_ter = float(total_gasto) >= float(nivel["min_total"])
        tem_role = role in member.roles

        if deve_ter and not tem_role:
            try:
                await member.add_roles(role, reason=f"Nível de gasto atingido: R$ {nivel['min_total']:.2f}")
            except discord.Forbidden:
                logger.warning(
                    "Sem permissao para adicionar cargo de nivel guild_id=%s user_id=%s role_id=%s",
                    guild.id,
                    user_id,
                    role.id
                )
            except Exception:
                logger.exception(
                    "Falha ao adicionar cargo de nivel guild_id=%s user_id=%s role_id=%s",
                    guild.id,
                    user_id,
                    role.id
                )
        elif (not deve_ter) and tem_role:
            try:
                await member.remove_roles(role, reason=f"Nível de gasto não atendido: R$ {nivel['min_total']:.2f}")
            except discord.Forbidden:
                logger.warning(
                    "Sem permissao para remover cargo de nivel guild_id=%s user_id=%s role_id=%s",
                    guild.id,
                    user_id,
                    role.id
                )
            except Exception:
                logger.exception(
                    "Falha ao remover cargo de nivel guild_id=%s user_id=%s role_id=%s",
                    guild.id,
                    user_id,
                    role.id
                )


def carregar_mm_taxa_metrics():
    if not os.path.exists(MM_TAXA_METRICS_FILE):
        return {}
    with open(MM_TAXA_METRICS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data
    return {}


def salvar_mm_taxa_metrics(data):
    with open(MM_TAXA_METRICS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def registrar_mm_taxa(guild_id, middle_id, valor_taxa):
    if middle_id is None:
        return
    try:
        valor = float(valor_taxa)
    except (TypeError, ValueError):
        return
    if valor <= 0:
        return

    data = carregar_mm_taxa_metrics()
    guild_key = str(guild_id)
    eventos = data.get(guild_key, [])
    if not isinstance(eventos, list):
        eventos = []

    agora = int(discord.utils.utcnow().timestamp())
    eventos.append(
        {
            "middle_id": int(middle_id),
            "valor_taxa": round(valor, 2),
            "ts": agora
        }
    )

    # Mantem 30 dias para não crescer indefinidamente.
    limite = agora - (30 * 24 * 60 * 60)
    eventos = [
        e for e in eventos
        if isinstance(e, dict) and _id_int(e.get("ts")) is not None and int(e.get("ts")) >= limite
    ]
    data[guild_key] = eventos
    salvar_mm_taxa_metrics(data)


def ranking_mm_taxa_24h(guild_id):
    data = carregar_mm_taxa_metrics()
    eventos = data.get(str(guild_id), [])
    if not isinstance(eventos, list):
        return []

    agora_utc = discord.utils.utcnow()
    agora_brt = agora_utc.astimezone(ZoneInfo("America/Sao_Paulo"))
    data_brt_atual = agora_brt.date()
    agora = int(agora_utc.timestamp())
    ranking = {}
    eventos_validos = []

    for evento in eventos:
        if not isinstance(evento, dict):
            continue
        ts = _id_int(evento.get("ts"))
        middle_id = _id_int(evento.get("middle_id"))
        try:
            valor = float(evento.get("valor_taxa"))
        except (TypeError, ValueError):
            continue
        if ts is None or middle_id is None or valor <= 0:
            continue
        dt_evento_brt = datetime.fromtimestamp(ts, tz=ZoneInfo("America/Sao_Paulo"))
        if dt_evento_brt.date() == data_brt_atual:
            ranking[middle_id] = ranking.get(middle_id, 0.0) + valor
        if ts >= (agora - (30 * 24 * 60 * 60)):
            eventos_validos.append(
                {"middle_id": middle_id, "valor_taxa": round(valor, 2), "ts": ts}
            )

    data[str(guild_id)] = eventos_validos
    salvar_mm_taxa_metrics(data)

    return sorted(ranking.items(), key=lambda x: x[1], reverse=True)


def ranking_mm_taxa_por_data(guild_id, data_brt_ref):
    data = carregar_mm_taxa_metrics()
    eventos = data.get(str(guild_id), [])
    if not isinstance(eventos, list):
        return []

    agora = int(discord.utils.utcnow().timestamp())
    ranking = {}
    eventos_validos = []

    for evento in eventos:
        if not isinstance(evento, dict):
            continue
        ts = _id_int(evento.get("ts"))
        middle_id = _id_int(evento.get("middle_id"))
        try:
            valor = float(evento.get("valor_taxa"))
        except (TypeError, ValueError):
            continue
        if ts is None or middle_id is None or valor <= 0:
            continue

        dt_evento_brt = datetime.fromtimestamp(ts, tz=ZoneInfo("America/Sao_Paulo"))
        if dt_evento_brt.date() == data_brt_ref:
            ranking[middle_id] = ranking.get(middle_id, 0.0) + valor
        if ts >= (agora - (30 * 24 * 60 * 60)):
            eventos_validos.append(
                {"middle_id": middle_id, "valor_taxa": round(valor, 2), "ts": ts}
            )

    data[str(guild_id)] = eventos_validos
    salvar_mm_taxa_metrics(data)
    return sorted(ranking.items(), key=lambda x: x[1], reverse=True)


def get_ultimo_envio_ranking_diario(guild_id):
    key = f"daily_rank_sent_{int(guild_id)}"
    with sqlite3.connect(SETTINGS_DB_FILE) as conn:
        row = conn.execute(
            "SELECT value FROM migration_meta WHERE key = ?",
            (key,)
        ).fetchone()
    if not row:
        return None
    return str(row[0])


def set_ultimo_envio_ranking_diario(guild_id, data_iso):
    key = f"daily_rank_sent_{int(guild_id)}"
    with sqlite3.connect(SETTINGS_DB_FILE) as conn:
        conn.execute(
            """
            INSERT INTO migration_meta (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, str(data_iso))
        )
        conn.commit()


def _normalizar_taxa_config(cfg):
    base = TAXA_PADRAO.copy()
    if isinstance(cfg, dict):
        base.update(cfg)
    return base


def carregar_taxa_config(guild_id=None):
    if guild_id is None:
        return TAXA_PADRAO.copy()

    cfg = TAXA_PADRAO.copy()
    with sqlite3.connect(SETTINGS_DB_FILE) as conn:
        rows = conn.execute(
            "SELECT faixa, valor FROM guild_taxa WHERE guild_id = ?",
            (int(guild_id),)
        ).fetchall()
        if not rows:
            rows = conn.execute(
                "SELECT faixa, valor FROM guild_taxa WHERE guild_id = 0"
            ).fetchall()
    for faixa, valor in rows:
        if faixa in cfg:
            try:
                cfg[faixa] = float(valor)
            except (TypeError, ValueError):
                continue
    return _normalizar_taxa_config(cfg)


def salvar_taxa_config_guild(guild_id, cfg):
    cfg_norm = _normalizar_taxa_config(cfg)
    with sqlite3.connect(SETTINGS_DB_FILE) as conn:
        for faixa, valor in cfg_norm.items():
            conn.execute(
                """
                INSERT INTO guild_taxa (guild_id, faixa, valor)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, faixa) DO UPDATE SET valor=excluded.valor
                """,
                (int(guild_id), faixa, float(valor))
            )
        conn.commit()


migrar_dados_legados()
init_settings_db()
migrate_json_configs_to_db()
carregar_estado_tickets_memoria()


def criar_embed_painel(guild_id=None):
    cfg = carregar_taxa_config(guild_id)
    taxa_ate_8 = float(cfg["ate_8_fixo"])
    taxa_100 = float(cfg["acima_100_fixo"])
    taxa_400 = float(cfg["acima_400_fixo"])
    taxa_700_pct = float(cfg["acima_700_percentual"]) * 100

    embed = discord.Embed(
        title="🔥 Sistema de Tickets 🔄",
        description=(
            "> Use esse sistema para solicitar seu **MIDDLE MAN**.\n\n"
            "**Taxas do Middleman — Vendas de Brainrots**\n\n"
            f"- *VALOR MÍNIMO DA **TAXA DO MIDDLE MAN** É R$ {taxa_ate_8:.2f}*\n"
            f"- *R$ {taxa_100:.2f} acima de R$ 100,00*\n"
            f"- *R$ {taxa_400:.2f} acima de R$ 400,00*\n"
            f"- *{taxa_700_pct:.2f}% acima de R$ 700,00*\n\n"
            "Clique abaixo para abrir um ticket."
        ),
        color=cor_paleta("info")
    )

    img_url = PANEL_DEFAULT_IMAGE_URL
    if guild_id is not None:
        img_cfg = get_painel_image_url(guild_id)
        if img_cfg:
            img_url = img_cfg

    embed.set_image(url=img_url)
    return embed

# ---------- TAXA DINÂMICA ----------
def calcular_taxa(valor, guild_id=None):
    cfg = carregar_taxa_config(guild_id)
    if valor > 700:
        return valor * float(cfg["acima_700_percentual"])
    elif valor > 400:
        return float(cfg["acima_400_fixo"])
    elif valor > 100:
        return float(cfg["acima_100_fixo"])
    elif valor > 8:
        return float(cfg["acima_8_fixo"])
    else:
        return float(cfg["ate_8_fixo"])


# ---------- BOT ----------
class botd(discord.Client):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self._painel_inicializado = False
        self._daily_ranking_task = None

    async def setup_hook(self):
        self.tree.on_error = self.on_app_command_error
        await self.tree.sync()

    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        logger.exception("[app_command_error] %s: %s", type(error).__name__, error)

        mensagem = "Ocorreu um erro ao executar este comando. Tente novamente."
        if isinstance(error, app_commands.CommandOnCooldown):
            mensagem = "Este comando está em cooldown. Aguarde e tente novamente."
        elif isinstance(error, app_commands.MissingPermissions):
            mensagem = "Você não tem permissão para usar este comando."
        elif isinstance(error, app_commands.CheckFailure):
            mensagem = "Você não pode usar este comando."

        try:
            if interaction.response.is_done():
                await interaction.followup.send(mensagem, ephemeral=True)
            else:
                await interaction.response.send_message(mensagem, ephemeral=True, delete_after=60)
        except Exception:
            pass

    async def _enviar_ranking_diario_no_log_admin(self, guild: discord.Guild, data_ref):
        if guild is None:
            return
        data_iso = data_ref.isoformat()
        if get_ultimo_envio_ranking_diario(guild.id) == data_iso:
            return

        channel_id = get_log_admin_canal_id(guild.id)
        if channel_id is None:
            return

        canal = guild.get_channel(channel_id)
        if canal is None:
            try:
                canal = await guild.fetch_channel(channel_id)
            except Exception:
                return
        if not isinstance(canal, discord.TextChannel):
            return

        ranking = ranking_mm_taxa_por_data(guild.id, data_ref)
        data_txt = data_ref.strftime("%d/%m/%Y")
        if ranking:
            linhas = []
            for i, (middle_id, total) in enumerate(ranking[:10], start=1):
                linhas.append(f"**{i}.** <@{middle_id}> — `R$ {total:.2f}`")
            descricao = "\n".join(linhas)
        else:
            descricao = "Nenhuma taxa registrada neste dia."

        embed = discord.Embed(
            title=f"📊 Ranking Final do Dia ({data_txt} - BRT)",
            description=descricao,
            color=cor_paleta("info")
        )
        embed.set_footer(text=f"Servidor: {guild.name}")
        try:
            await canal.send(embed=embed)
            set_ultimo_envio_ranking_diario(guild.id, data_iso)
        except Exception:
            logger.exception(
                "Falha ao enviar ranking diario guild_id=%s channel_id=%s data=%s",
                guild.id,
                channel_id,
                data_iso
            )

    async def _loop_ranking_diario(self):
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                agora_brt = discord.utils.utcnow().astimezone(ZoneInfo("America/Sao_Paulo"))
                proxima_virada = (agora_brt + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
                espera = max(5, (proxima_virada - agora_brt).total_seconds())
                await asyncio.sleep(espera)

                data_ref = (discord.utils.utcnow().astimezone(ZoneInfo("America/Sao_Paulo")) - timedelta(days=1)).date()
                for guild in list(self.guilds):
                    await self._enviar_ranking_diario_no_log_admin(guild, data_ref)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Falha no loop de ranking diario")
                await asyncio.sleep(30)

    async def _buscar_canal_texto(self, canal_id: int):
        canal = self.get_channel(canal_id)
        if isinstance(canal, discord.TextChannel):
            return canal
        try:
            canal = await self.fetch_channel(canal_id)
            if isinstance(canal, discord.TextChannel):
                return canal
        except Exception:
            return None
        return None

    def _detectar_criador_ticket(self, canal: discord.TextChannel, excluir_ids=None):
        excluir_ids = set(excluir_ids or [])
        for alvo, overwrite in canal.overwrites.items():
            if not isinstance(alvo, discord.Member):
                continue
            if alvo.bot or alvo.id in excluir_ids:
                continue
            if overwrite.view_channel is True:
                return alvo
        return None

    async def _avisar_aceite_pix_brainrot(self, canal, comprador, vendedor):
        aceite_canal_id = get_aceite_canal_id(canal.guild.id)
        if not aceite_canal_id:
            await enviar_fluxo(
                canal,
                "⚠️ Canal de aceite não configurado. Um administrador deve usar `/setaceite`.",
                cor=cor_paleta("erro")
            )
            return

        try:
            channel_id = int(aceite_canal_id)
        except (TypeError, ValueError):
            channel_id = None

        aceite_channel = None
        if channel_id is not None:
            aceite_channel = canal.guild.get_channel(channel_id)
            if aceite_channel is None:
                try:
                    aceite_channel = await canal.guild.fetch_channel(channel_id)
                except (discord.HTTPException, discord.NotFound, discord.Forbidden):
                    aceite_channel = None

        if not isinstance(aceite_channel, discord.TextChannel):
            await enviar_fluxo(
                canal,
                "⚠️ Canal de aceite não configurado. Um administrador deve usar `/setaceite`.",
                cor=cor_paleta("erro")
            )
            return

        ticket_kind = ticket_type.get(canal.id, "pix")
        tipo_middle = "Taxa Brain Rot" if ticket_kind == "brainrot" else "Taxa Pix"
        role_middle = get_middle_role(canal.guild)
        mencao_middle = role_middle.mention if role_middle else "@Middle Man"
        try:
            await aceite_channel.send(
                content=mencao_middle,
                embed=embed_fluxo(
                    f"Ticket aguardando MM ({tipo_middle}): {canal.mention}",
                    cor=cor_paleta("aviso")
                ),
                view=MiddlemanAcceptView(canal, comprador, vendedor)
            )
        except discord.Forbidden:
            await enviar_fluxo(
                canal,
                "⚠️ Sem permissão para enviar no canal de aceite configurado. "
                "Verifique se o bot tem permissão de envio de mensagens.",
                cor=cor_paleta("erro")
            )

    async def _avisar_aceite_trade(self, canal, pessoa1, pessoa2):
        aceite_canal_id = get_aceite_canal_id(canal.guild.id)
        if not aceite_canal_id:
            await enviar_fluxo(
                canal,
                "⚠️ Canal de aceite não configurado. Um administrador deve usar `/setaceite`.",
                cor=cor_paleta("erro")
            )
            return

        try:
            channel_id = int(aceite_canal_id)
        except (TypeError, ValueError):
            channel_id = None

        aceite_channel = None
        if channel_id is not None:
            aceite_channel = canal.guild.get_channel(channel_id)
            if aceite_channel is None:
                try:
                    aceite_channel = await canal.guild.fetch_channel(channel_id)
                except (discord.HTTPException, discord.NotFound, discord.Forbidden):
                    aceite_channel = None

        if not isinstance(aceite_channel, discord.TextChannel):
            await enviar_fluxo(
                canal,
                "⚠️ Canal de aceite não configurado. Um administrador deve usar `/setaceite`.",
                cor=cor_paleta("erro")
            )
            return

        role_middle = get_middle_role(canal.guild)
        mencao_middle = role_middle.mention if role_middle else "@Middle Man"
        try:
            await aceite_channel.send(
                content=mencao_middle,
                embed=embed_fluxo(
                    f"Ticket aguardando MM (Trade): {canal.mention}",
                    cor=cor_paleta("aviso")
                ),
                view=MiddlemanAcceptTradeView(canal, pessoa1, pessoa2)
            )
        except discord.Forbidden:
            await enviar_fluxo(
                canal,
                "⚠️ Sem permissão para enviar no canal de aceite configurado. "
                "Verifique se o bot tem permissão de envio de mensagens.",
                cor=cor_paleta("erro")
            )

    async def _recuperar_ticket(self, canal: discord.TextChannel):
        kind = ticket_type.get(canal.id, "pix")
        middle_id = ticket_middleman.get(canal.id)

        await enviar_fluxo(
            canal,
            "🔄 Bot reiniciado. Fluxo deste ticket foi recarregado.",
            view=FecharTicketView(canal),
            cor=cor_paleta("primario")
        )

        if kind in {"pix", "brainrot"}:
            partes = obter_partes_ticket(canal)
            if not partes:
                criador = self._detectar_criador_ticket(canal)
                if criador:
                    view_setup = TradeSetupView(canal, criador)
                    msg = await enviar_fluxo(
                        canal,
                        "🔄 Bot reiniciado. Se necessário, refaça a definição de comprador e vendedor:",
                        view=view_setup,
                        cor=cor_paleta("primario")
                    )
                    view_setup.message = msg
                return

            comprador = partes["comprador"]
            vendedor = partes["vendedor"]
            estado = _estado_negociacao(canal.id)

            if middle_id is None:
                if estado is None:
                    iniciar_negociacao_ticket(canal.id, comprador, vendedor)
                    estado = _estado_negociacao(canal.id)
                if estado:
                    estado["etapa"] = "aguardando_middle_pix"
                await self._avisar_middles_no_canal(canal, comprador, vendedor, ticket_kind=kind)
                return

            middle = canal.guild.get_member(int(middle_id))
            if middle is None:
                ticket_middleman.pop(canal.id, None)
                salvar_estado_tickets()
                if estado is None:
                    iniciar_negociacao_ticket(canal.id, comprador, vendedor)
                    estado = _estado_negociacao(canal.id)
                elif estado.get("etapa") not in {"aguardando_middle_pix", "coleta_dados"}:
                    estado["etapa"] = "aguardando_middle_pix"
                await self._avisar_middles_no_canal(canal, comprador, vendedor, ticket_kind=kind)
                return

            if estado is not None and estado.get("etapa") == "finalizado":
                return
            if estado and (estado.get("confirm_msg_id") or estado.get("etapa") not in {"coleta_dados", "aguardando_middle_pix"}):
                return

            await canal.set_permissions(middle, view_channel=True)
            await self._iniciar_fluxo_pix_brainrot(canal, comprador, vendedor, reiniciado=True)
            return

        if kind == "trade":
            partes_trade = obter_partes_trade(canal)
            if not partes_trade:
                criador = self._detectar_criador_ticket(canal)
                if criador:
                    view_trade = TradeSetupTradeView(canal, criador)
                    msg = await enviar_fluxo(
                        canal,
                        "🔄 Bot reiniciado. Se necessário, refaça a seleção da pessoa da troca:",
                        view=view_trade,
                        cor=cor_paleta("primario")
                    )
                    view_trade.message = msg
                return

            pessoa1 = partes_trade["pessoa1"]
            pessoa2 = partes_trade["pessoa2"]

            if middle_id is None:
                embed = discord.Embed(
                    title="⏳ Aguardando Middle Man",
                    description="🔄 Bot reiniciado. Um middle irá aceitar o ticket em breve...",
                    color=cor_paleta("aviso")
                )
                msg_loading = await canal.send(embed=embed)
                ticket_loading_msg[canal.id] = msg_loading
                await self._avisar_aceite_trade(canal, pessoa1, pessoa2)
                return

            middle = canal.guild.get_member(int(middle_id))
            if middle is None:
                ticket_middleman.pop(canal.id, None)
                salvar_estado_tickets()
                embed = discord.Embed(
                    title="⏳ Aguardando Middle Man",
                    description="🔄 O middle anterior não está disponível. Aguardando novo aceite...",
                    color=cor_paleta("aviso")
                )
                msg_loading = await canal.send(embed=embed)
                ticket_loading_msg[canal.id] = msg_loading
                await self._avisar_aceite_trade(canal, pessoa1, pessoa2)
                return

            await canal.set_permissions(middle, view_channel=True)
            iniciar_negociacao_trade(canal.id, pessoa1, pessoa2)
            estado = _estado_negociacao(canal.id)
            if estado and not estado.get("trade_etapa"):
                estado["trade_etapa"] = "aguardando_escolha_taxa_trade"
            await enviar_fluxo(
                canal,
                "🔄 Bot reiniciado. Continue escolhendo a taxa da trade:",
                view=TradeTaxaEscolhaView(canal, pessoa1, pessoa2, middle.id),
                cor=cor_paleta("aviso")
            )
            return

    async def on_ready(self):
        logger.info("Bot %s ON", self.user)
        if self._daily_ranking_task is None or self._daily_ranking_task.done():
            self._daily_ranking_task = asyncio.create_task(self._loop_ranking_diario())

        # Tentativa de envio pendente caso tenha reiniciado após a virada.
        try:
            data_ref = (discord.utils.utcnow().astimezone(ZoneInfo("America/Sao_Paulo")) - timedelta(days=1)).date()
            for guild in list(self.guilds):
                await self._enviar_ranking_diario_no_log_admin(guild, data_ref)
        except Exception:
            logger.exception("Falha ao verificar envio pendente de ranking diario")

        if self._painel_inicializado:
            return
        self._painel_inicializado = True

        config = carregar_painel_config()
        for guild_key, entry in config.items():
            guild_id = _id_int(guild_key)
            if guild_id is None:
                continue

            channel_id = entry.get("channel_id") if isinstance(entry, dict) else entry
            try:
                channel_id = int(channel_id)
                canal = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
            except Exception:
                continue

            if not isinstance(canal, discord.TextChannel):
                continue

            try:
                await canal.purge(limit=50, check=lambda m: m.author == self.user)
                await canal.send(embed=criar_embed_painel(canal.guild.id), view=TicketView())
            except discord.Forbidden:
                logger.warning("Sem permissao para gerenciar mensagens no canal %s", canal.id)
            except discord.HTTPException as e:
                logger.warning("Falha ao atualizar painel no canal %s: %s", canal.id, e)

        removidos = 0
        recuperados = 0
        for canal_id in list(ticket_type.keys()):
            canal = await self._buscar_canal_texto(canal_id)
            if not canal:
                remover_estado_ticket(canal_id)
                removidos += 1
                continue

            try:
                await self._recuperar_ticket(canal)
                recuperados += 1
            except Exception:
                logger.exception("Falha ao recuperar ticket canal_id=%s", canal_id)

        if removidos or recuperados:
            logger.info("Recuperacao de tickets concluida: recuperados=%s removidos=%s", recuperados, removidos)


async def enviar_qr_fluxo_pix(canal, modo, dados):
    middle_id = _id_int(dados.get("middle_id"))
    if middle_id is None:
        return False, "⚠️ Nenhum Middle vinculado a este ticket."

    pix_info = get_pix_data(middle_id)
    pix_key = pix_info.get("chave")
    pix_nome = pix_info.get("nome") or "Não informado"

    if not pix_key:
        return False, "❌ Middle não cadastrou chave PIX."
    if not validar_chave_pix(pix_key):
        return False, "❌ Middle cadastrou uma chave PIX inválida."

    if modo == "taxa_comprador":
        valor = float(dados["valor"])
        taxa = float(dados["taxa"])
        comprador = dados["comprador"]
        vendedor = dados["vendedor"]
        total = valor + taxa

        qr = gerar_qrcode_pix(pix_key, total)
        pix_copia_cola = gerar_payload_pix(pix_key, valor=f"{total:.2f}")
        file = discord.File(fp=qr, filename="pix_total.png")
        embed_qr = discord.Embed(
            title="💰 Pagamento total (item + taxa)",
            description=(
                f"Titular: {pix_nome}\n"
                f"Código Pix: `{pix_copia_cola}`\n"
                f"Valor do item: R$ {valor:.2f}\n"
                f"Taxa MM: R$ {taxa:.2f}\n"
                f"Total: R$ {total:.2f}"
            ),
            color=cor_paleta("sucesso")
        )
        embed_qr.set_image(url="attachment://pix_total.png")
        await canal.send(embed=embed_qr, file=file)
        await enviar_fluxo(
            canal,
            "⏳ Aguarde o Middle Man confirmar que recebeu o valor do *Brainrot* e o valor da *Taxa*...",
            view=ConfirmarPagamentoView(canal, comprador, vendedor, pix_copia_cola),
            cor=cor_paleta("aviso")
        )
        return True, None

    if modo == "taxa_vendedor":
        valor = float(dados["valor"])
        taxa = float(dados["taxa"])
        comprador = dados["comprador"]
        vendedor = dados["vendedor"]

        qr_item = gerar_qrcode_pix(pix_key, valor)
        pix_copia_cola_item = gerar_payload_pix(pix_key, valor=f"{valor:.2f}")
        file_item = discord.File(fp=qr_item, filename="pix_item.png")
        embed_item = discord.Embed(
            title="🛒 QR do comprador (valor do item)",
            description=(
                f"Titular: {pix_nome}\n"
                f"Código Pix: `{pix_copia_cola_item}`\n"
                f"Valor: R$ {valor:.2f}"
            ),
            color=cor_paleta("info")
        )
        embed_item.set_image(url="attachment://pix_item.png")
        await canal.send(embed=embed_item, file=file_item)
        await canal.send(view=PixCopiaColaView(pix_copia_cola_item))

        qr_taxa = gerar_qrcode_pix(pix_key, taxa)
        pix_copia_cola_taxa = gerar_payload_pix(pix_key, valor=f"{taxa:.2f}")
        file_taxa = discord.File(fp=qr_taxa, filename="pix_taxa.png")
        embed_taxa = discord.Embed(
            title="💸 QR do vendedor (taxa do middle)",
            description=(
                f"Titular: {pix_nome}\n"
                f"Código Pix: `{pix_copia_cola_taxa}`\n"
                f"Taxa: R$ {taxa:.2f}"
            ),
            color=cor_paleta("aviso")
        )
        embed_taxa.set_image(url="attachment://pix_taxa.png")
        await canal.send(embed=embed_taxa, file=file_taxa)
        await canal.send(view=PixCopiaColaView(pix_copia_cola_taxa))
        await enviar_fluxo(
            canal,
            "⏳ Aguarde o Middle Man confirmar que recebeu o valor do *Brainrot* e o valor da *Taxa*...",
            view=ConfirmarPagamentoView(canal, comprador, vendedor, pix_copia_cola_item),
            cor=cor_paleta("aviso")
        )
        return True, None

    if modo == "brainrot_item":
        valor = float(dados["valor"])
        comprador = dados["comprador"]
        vendedor = dados["vendedor"]

        qr = gerar_qrcode_pix(pix_key, valor)
        pix_copia_cola = gerar_payload_pix(pix_key, valor=f"{valor:.2f}")
        file = discord.File(fp=qr, filename="pix_item_brainrot.png")
        embed_qr = discord.Embed(
            title="💰 Pagamento do item (após taxa em Brainrot)",
            description=(
                f"Titular: {pix_nome}\n"
                f"Código Pix: `{pix_copia_cola}`\n"
                f"Valor confirmado: R$ {valor:.2f}"
            ),
            color=cor_paleta("sucesso")
        )
        embed_qr.set_image(url="attachment://pix_item_brainrot.png")
        await canal.send(embed=embed_qr, file=file)
        await enviar_fluxo(
            canal,
            "⏳ Aguardando pagamento do comprador...",
            view=ConfirmarPagamentoBrainrotPixView(canal, comprador, vendedor, pix_copia_cola),
            cor=cor_paleta("aviso")
        )
        return True, None

    if modo == "trade_pix":
        valor = float(dados["valor"])
        pessoa1 = dados["pessoa1"]
        pessoa2 = dados["pessoa2"]

        qr = gerar_qrcode_pix(pix_key, valor)
        pix_copia_cola = gerar_payload_pix(pix_key, valor=f"{valor:.2f}")
        file = discord.File(fp=qr, filename="trade_pix.png")
        embed = discord.Embed(
            title="Cobrança Pix da Trade",
            description=(
                f"Titular: {pix_nome}\n"
                f"Código Pix: `{pix_copia_cola}`\n"
                f"Valor: R$ {valor:.2f}"
            ),
            color=cor_paleta("sucesso")
        )
        embed.set_image(url="attachment://trade_pix.png")
        await canal.send(embed=embed, file=file)
        await enviar_fluxo(
            canal,
            "Aguardando confirmação de pagamento...",
            view=ConfirmarPagamentoTradePixView(canal, pessoa1, pessoa2, middle_id, pix_copia_cola),
            cor=cor_paleta("aviso")
        )
        return True, None

    return False, "❌ Fluxo de reenvio de QR não reconhecido."


class ReenviarQrPixView(discord.ui.View):
    def __init__(self, canal, modo, dados):
        super().__init__(timeout=1800)
        self.canal = canal
        self.modo = modo
        self.dados = dados

    @discord.ui.button(label="Tentar novamente enviar QR", style=ESTILO_BOTAO["primario"])
    async def tentar_novamente(self, interaction, button):
        if await em_cooldown(interaction, "reenviar_qr_pix", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return

        middle_id = ticket_middleman.get(self.canal.id)
        if interaction.user.id != middle_id:
            await interaction.response.send_message(
                "Apenas o Middle deste ticket pode clicar neste botão.",
                ephemeral=True,
                delete_after=60
            )
            return

        estado = _estado_negociacao(self.canal.id)
        etapa = estado.get("etapa") if estado else None
        if self.modo in {"taxa_comprador", "taxa_vendedor"} and etapa != "aguardando_escolha_taxa":
            await interaction.response.send_message(
                "Esta etapa já foi processada.",
                ephemeral=True,
                delete_after=60
            )
            return
        if self.modo == "brainrot_item" and etapa != "aguardando_pagamento_brainrot_pix":
            await interaction.response.send_message(
                "Esta etapa já foi processada.",
                ephemeral=True,
                delete_after=60
            )
            return
        if self.modo == "trade_pix":
            trade_etapa = estado.get("trade_etapa") if estado else None
            if trade_etapa != "aguardando_valor_pix_trade":
                await interaction.response.send_message(
                    "Esta etapa já foi processada.",
                    ephemeral=True,
                    delete_after=60
                )
                return

        await interaction.response.defer(ephemeral=True)

        dados = dict(self.dados)
        dados["middle_id"] = middle_id
        ok, erro = await enviar_qr_fluxo_pix(self.canal, self.modo, dados)
        if not ok:
            await interaction.followup.send(
                f"{erro}\nUse `/setpix` e tente novamente.",
                ephemeral=True
            )
            return

        try:
            await interaction.message.edit(view=None)
        except Exception:
            pass

        if self.modo == "trade_pix" and estado:
            estado["trade_etapa"] = "aguardando_pagamento_pix_trade"

        await interaction.followup.send("✅ QR reenviado com sucesso.", ephemeral=True)


class PixCopiaColaView(discord.ui.View):
    def __init__(self, payload):
        super().__init__(timeout=None)
        self.payload = payload

    @discord.ui.button(label="📋 Copiar código Pix", style=ESTILO_BOTAO["sucesso"])
    async def copiar_codigo(self, interaction, button):
        await interaction.response.send_message(
            f"`{self.payload}`",
            ephemeral=True
        )


# ---------- TAXA VIEW ----------
class TaxaView(discord.ui.View):
    def __init__(self, valor, comprador, vendedor, guild_id=None):
        super().__init__(timeout=None)
        self.valor = valor
        self.taxa = calcular_taxa(valor, guild_id)
        self.comprador = comprador
        self.vendedor = vendedor
        self.guild_id = guild_id

    async def mostrar(self, interaction, mensagem):
        try:
            await interaction.message.edit(view=None)
        except Exception:
            pass
        await enviar_fluxo(
            interaction.channel,
            mensagem,
            cor=cor_paleta("destaque")
        )
        await interaction.response.send_message("Taxa definida.", ephemeral=True, delete_after=60)
        self.stop()

    @discord.ui.button(label="Comprador paga", style=ESTILO_BOTAO["sucesso"])
    async def comprador(self, interaction, button):
        if await em_cooldown(interaction, "taxa_escolha_pagador", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return

        estado = _estado_negociacao(interaction.channel.id)
        if estado and estado.get("etapa") != "aguardando_escolha_taxa":
            await interaction.response.send_message(
                "Esta etapa já foi processada.",
                ephemeral=True,
                delete_after=60
            )
            return

        if interaction.user != self.comprador:
            await interaction.response.send_message(
                "Somente o comprador pode escolher quem paga a taxa.",
                ephemeral=True, delete_after=60
            )
            return
        if estado:
            estado["etapa"] = "escolha_taxa_processando"

        total = self.valor + self.taxa

        msg = (
            f"💰 Valor do item: R$ {self.valor:.2f}\n"
            f"💸 Taxa MM: R$ {self.taxa:.2f}\n\n"
            f"🛒 Comprador enviará: R$ {total:.2f}\n"
            f"🏷️ Vendedor receberá: R$ {self.valor:.2f}"
        )

        await self.mostrar(interaction, msg)

        middle_id = ticket_middleman.get(interaction.channel.id)
        ok, erro = await enviar_qr_fluxo_pix(
            interaction.channel,
            "taxa_comprador",
            {
                "middle_id": middle_id,
                "valor": self.valor,
                "taxa": self.taxa,
                "comprador": self.comprador,
                "vendedor": self.vendedor
            }
        )
        if not ok:
            if estado:
                estado["etapa"] = "aguardando_escolha_taxa"
            await enviar_fluxo(
                interaction.channel,
                f"{erro}\nMiddle: use `/setpix` e clique no botão abaixo para tentar novamente.",
                view=ReenviarQrPixView(
                    interaction.channel,
                    "taxa_comprador",
                    {
                        "valor": self.valor,
                        "taxa": self.taxa,
                        "comprador": self.comprador,
                        "vendedor": self.vendedor
                    }
                ),
                cor=cor_paleta("erro")
            )
            return
        if estado:
            estado["etapa"] = "aguardando_pagamento_middle"


    @discord.ui.button(label="Vendedor paga", style=ESTILO_BOTAO["perigo"])
    async def vendedor(self, interaction, button):
        if await em_cooldown(interaction, "taxa_escolha_pagador", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return

        estado = _estado_negociacao(interaction.channel.id)
        if estado and estado.get("etapa") != "aguardando_escolha_taxa":
            await interaction.response.send_message(
                "Esta etapa já foi processada.",
                ephemeral=True,
                delete_after=60
            )
            return

        if interaction.user != self.comprador:
            await interaction.response.send_message(
                "Somente o comprador pode escolher quem paga a taxa.",
                ephemeral=True, delete_after=60
            )
            return
        if estado:
            estado["etapa"] = "escolha_taxa_processando"

        msg = (
            f"💰 Valor do item: R$ {self.valor:.2f}\n"
            f"💸 Taxa MM: R$ {self.taxa:.2f}\n\n"
            f"🛒 Comprador enviará: R$ {self.valor:.2f}\n"
            f"🏷️ Vendedor pagará taxa: R$ {self.taxa:.2f}\n"
            f"🏷️ Vendedor receberá: R$ {self.valor:.2f}"
        )

        await self.mostrar(interaction, msg)

        middle_id = ticket_middleman.get(interaction.channel.id)
        ok, erro = await enviar_qr_fluxo_pix(
            interaction.channel,
            "taxa_vendedor",
            {
                "middle_id": middle_id,
                "valor": self.valor,
                "taxa": self.taxa,
                "comprador": self.comprador,
                "vendedor": self.vendedor
            }
        )
        if not ok:
            if estado:
                estado["etapa"] = "aguardando_escolha_taxa"
            await enviar_fluxo(
                interaction.channel,
                f"{erro}\nMiddle: use `/setpix` e clique no botão abaixo para tentar novamente.",
                view=ReenviarQrPixView(
                    interaction.channel,
                    "taxa_vendedor",
                    {
                        "valor": self.valor,
                        "taxa": self.taxa,
                        "comprador": self.comprador,
                        "vendedor": self.vendedor
                    }
                ),
                cor=cor_paleta("erro")
            )
            return
        if estado:
            estado["etapa"] = "aguardando_pagamento_middle"

class ConfirmarPagamentoView(discord.ui.View):
    def __init__(self, canal, comprador, vendedor, pix_copia_cola=None):
        super().__init__(timeout=None)
        self.canal = canal
        self.comprador = comprador
        self.vendedor = vendedor
        self.pix_copia_cola = pix_copia_cola
        if not self.pix_copia_cola:
            self.remove_item(self.copiar_codigo)

    @discord.ui.button(label="📋 Copiar código Pix", style=ESTILO_BOTAO["sucesso"], row=0)
    async def copiar_codigo(self, interaction, button):
        await interaction.response.send_message(
            f"`{self.pix_copia_cola}`",
            ephemeral=True
        )

    @discord.ui.button(label="Confirmar Taxa ( MM )", style=ESTILO_BOTAO["aviso"], row=0)
    async def confirmar_pagamento(self, interaction, button):
        if await em_cooldown(interaction, "confirmar_pagamento_pix", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return
        lock = await ticket_lock_or_wait_msg(interaction, self.canal.id)
        if lock is None:
            return
        async with lock:

            middle_id = ticket_middleman.get(self.canal.id)

            if interaction.user.id != middle_id:
                await interaction.response.send_message(
                    "Apenas o Middle pode confirmar o pagamento.",
                    ephemeral=True, delete_after=60
                )
                return

            estado = _estado_negociacao(self.canal.id)
            if estado and estado.get("etapa") != "aguardando_pagamento_middle":
                await interaction.response.send_message(
                    "Esta etapa já foi processada.",
                    ephemeral=True,
                    delete_after=60
                )
                return
            if estado:
                estado["etapa"] = "pagamento_middle_processando"

            await interaction.response.defer()
            try:
                await interaction.message.delete()
            except discord.NotFound:
                pass

            if estado:
                estado["etapa"] = "aguardando_confirmacao_entrega"
            await enviar_fluxo(
                self.canal,
                f"📦 {self.comprador.mention}, confirme que recebeu o Brainrot:",
                view=ConfirmarEntregaView(self.canal, self.comprador, self.vendedor),
                cor=cor_paleta("destaque")
            )

class ConfirmarTaxaBrainrotView(discord.ui.View):
    def __init__(self, canal, valor, comprador, vendedor):
        super().__init__(timeout=None)
        self.canal = canal
        self.valor = valor
        self.comprador = comprador
        self.vendedor = vendedor

    @discord.ui.button(label="Recebi a taxa em Brainrot", style=ESTILO_BOTAO["sucesso"])
    async def confirmar_taxa_brainrot(self, interaction, button):
        if await em_cooldown(interaction, "confirmar_taxa_brainrot", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return
        lock = await ticket_lock_or_wait_msg(interaction, self.canal.id)
        if lock is None:
            return
        async with lock:

            middle_id = ticket_middleman.get(self.canal.id)

            if interaction.user.id != middle_id:
                await interaction.response.send_message(
                    "Apenas o Middle pode confirmar o recebimento da taxa.",
                    ephemeral=True, delete_after=60
                )
                return

            estado = _estado_negociacao(self.canal.id)
            if estado and estado.get("etapa") != "aguardando_taxa_brainrot_middle":
                await interaction.response.send_message(
                    "Esta etapa já foi processada.",
                    ephemeral=True,
                    delete_after=60
                )
                return
            if estado:
                estado["etapa"] = "taxa_brainrot_processando"

            await interaction.response.defer()
            try:
                await interaction.message.delete()
            except discord.NotFound:
                pass

            ok, erro = await enviar_qr_fluxo_pix(
                self.canal,
                "brainrot_item",
                {
                    "middle_id": middle_id,
                    "valor": self.valor,
                    "comprador": self.comprador,
                    "vendedor": self.vendedor
                }
            )
            if not ok:
                if estado:
                    estado["etapa"] = "aguardando_pagamento_brainrot_pix"
                await enviar_fluxo(
                    self.canal,
                    f"{erro}\nMiddle: use `/setpix` e clique no botão abaixo para tentar novamente.",
                    view=ReenviarQrPixView(
                        self.canal,
                        "brainrot_item",
                        {
                            "valor": self.valor,
                            "comprador": self.comprador,
                            "vendedor": self.vendedor
                        }
                    ),
                    cor=cor_paleta("erro")
                )
                return
            if estado:
                estado["etapa"] = "aguardando_pagamento_brainrot_pix"

class ConfirmarPagamentoBrainrotPixView(discord.ui.View):
    def __init__(self, canal, comprador, vendedor, pix_copia_cola=None):
        super().__init__(timeout=None)
        self.canal = canal
        self.comprador = comprador
        self.vendedor = vendedor
        self.pix_copia_cola = pix_copia_cola
        if not self.pix_copia_cola:
            self.remove_item(self.copiar_codigo)

    @discord.ui.button(label="📋 Copiar código Pix", style=ESTILO_BOTAO["sucesso"], row=0)
    async def copiar_codigo(self, interaction, button):
        await interaction.response.send_message(
            f"`{self.pix_copia_cola}`",
            ephemeral=True
        )

    @discord.ui.button(label="Confirmar Taxa ( MM )", style=ESTILO_BOTAO["aviso"], row=0)
    async def confirmar_pagamento(self, interaction, button):
        try:
            if await em_cooldown(interaction, "confirmar_pagamento_brainrot_pix", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
                return
            lock = await ticket_lock_or_wait_msg(interaction, self.canal.id)
            if lock is None:
                return
            async with lock:

                middle_id = ticket_middleman.get(self.canal.id)

                if interaction.user.id != middle_id:
                    await interaction.response.send_message(
                        "Apenas o Middle pode confirmar o pagamento.",
                        ephemeral=True, delete_after=60
                    )
                    return

                estado = _estado_negociacao(self.canal.id)
                if estado and estado.get("etapa") != "aguardando_pagamento_brainrot_pix":
                    await interaction.response.send_message(
                        "Esta etapa já foi processada.",
                        ephemeral=True,
                        delete_after=60
                    )
                    return
                if estado:
                    estado["etapa"] = "pagamento_brainrot_pix_processando"

                await interaction.response.defer()
                try:
                    await interaction.message.delete()
                except discord.NotFound:
                    pass

                if estado:
                    estado["etapa"] = "aguardando_confirmacao_entrega"
                await enviar_fluxo(
                    self.canal,
                    f"📦 {self.comprador.mention}, confirme que recebeu o Brainrot:",
                    view=ConfirmarEntregaView(self.canal, self.comprador, self.vendedor),
                    cor=cor_paleta("destaque")
                )
        except Exception:
            logger.exception("Erro em ConfirmarPagamentoBrainrotPixView canal_id=%s", self.canal.id)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Ocorreu um erro ao confirmar pagamento em PIX. Tente novamente.",
                    ephemeral=True, delete_after=60
                )
            else:
                await interaction.followup.send(
                    "Ocorreu um erro ao confirmar pagamento em PIX. Tente novamente.",
                    ephemeral=True
                )

class ConfirmarEntregaView(discord.ui.View):
    def __init__(self, canal, comprador, vendedor):
        super().__init__(timeout=None)
        self.canal = canal
        self.comprador = comprador
        self.vendedor = vendedor

    @discord.ui.button(label="📦 Recebi o Brainrot", style=ESTILO_BOTAO["sucesso"])
    async def confirmar_item(self, interaction, button):
        if await em_cooldown(interaction, "confirmar_recebimento_item", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return
        lock = await ticket_lock_or_wait_msg(interaction, self.canal.id)
        if lock is None:
            return
        async with lock:

            if interaction.user != self.comprador:
                await interaction.response.send_message(
                    "Apenas o comprador pode confirmar.",
                    ephemeral=True, delete_after=60
                )
                return
            estado = _estado_negociacao(self.canal.id)
            if estado and estado.get("etapa") != "aguardando_confirmacao_entrega":
                await interaction.response.send_message(
                    "Esta etapa já foi processada.",
                    ephemeral=True,
                    delete_after=60
                )
                return
            if estado:
                estado["etapa"] = "entrega_confirmada_processando"

            await interaction.response.defer()
            await interaction.message.delete()

            if estado:
                estado["etapa"] = "aguardando_envio_pix_vendedor"
            await enviar_fluxo(
                self.canal,
                f"{self.vendedor.mention}, envie sua chave Pix para que o Middle Man possa enviar o pix do Brainrot",
                view=EnviarPixView(self.canal, self.vendedor),
                cor=cor_paleta("aviso")
            )

class PixModal(discord.ui.Modal, title="Enviar chave Pix"):
    chave = discord.ui.TextInput(label="Digite sua chave Pix")

    def __init__(self, canal, vendedor):
        super().__init__()
        self.canal = canal
        self.vendedor = vendedor

    async def on_submit(self, interaction):

        chave = self.chave.value
        estado = _estado_negociacao(self.canal.id)
        if estado and estado.get("etapa") != "aguardando_envio_pix_vendedor":
            await interaction.response.send_message(
                "Esta etapa já foi processada.",
                ephemeral=True, delete_after=60
            )
            return

        await enviar_fluxo(
            self.canal,
            f"💳 Pix do vendedor:\n*Apenas confirme o pagamento quando o Middle Man enviar o seu pix*\n`{chave}`",
            view=ConfirmarRecebimentoView(self.canal, self.vendedor),
            cor=cor_paleta("info")
        )
        if estado:
            estado["etapa"] = "aguardando_confirmacao_recebimento_vendedor"

        await interaction.response.send_message("Pix enviado.", ephemeral=True, delete_after=60)

class EnviarPixView(discord.ui.View):
    def __init__(self, canal, vendedor):
        super().__init__(timeout=None)
        self.canal = canal
        self.vendedor = vendedor

    @discord.ui.button(label="💳 Enviar meu Pix", style=ESTILO_BOTAO["primario"])
    async def enviar_pix(self, interaction, button):

        if interaction.user != self.vendedor:
            await interaction.response.send_message(
                "Somente o vendedor pode enviar Pix.",
                ephemeral=True, delete_after=60
            )
            return

        estado = _estado_negociacao(self.canal.id)
        if estado and estado.get("etapa") != "aguardando_envio_pix_vendedor":
            await interaction.response.send_message(
                "Esta etapa já foi processada.",
                ephemeral=True, delete_after=60
            )
            return

        await interaction.response.send_modal(
            PixModal(self.canal, self.vendedor)
        )

class ConfirmarRecebimentoView(discord.ui.View):
    def __init__(self, canal, vendedor):
        super().__init__(timeout=None)
        self.canal = canal
        self.vendedor = vendedor

    @discord.ui.button(label="💰 Recebi o pagamento", style=ESTILO_BOTAO["sucesso"])
    async def confirmar_recebimento(self, interaction, button):
        if await em_cooldown(interaction, "confirmar_recebimento_vendedor", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return
        lock = await ticket_lock_or_wait_msg(interaction, self.canal.id)
        if lock is None:
            return
        async with lock:

            if interaction.user != self.vendedor:
                await interaction.response.send_message(
                    "Somente vendedor confirma.",
                    ephemeral=True, delete_after=60
                )
                return

            estado = _estado_negociacao(self.canal.id)
            if estado and estado.get("etapa") != "aguardando_confirmacao_recebimento_vendedor":
                await interaction.response.send_message(
                    "Esta etapa já foi processada.",
                    ephemeral=True,
                    delete_after=60
                )
                return
            if estado:
                estado["etapa"] = "finalizacao_processando"

            await interaction.response.defer()
            await interaction.message.delete()

            embed_finalizado = discord.Embed(
                title="Trade Finalizada",
                description=(
                    "✅ Intermediação finalizada com sucesso!\n\n"
                    "Obrigado por utilizar nosso sistema de middle man."
                ),
                color=cor_paleta("sucesso")
            )

            await self.canal.send(embed=embed_finalizado, view=FinalizarTicketView(self.canal))
            if estado:
                estado["etapa"] = "finalizado"

class FecharTicketView(discord.ui.View):
    def __init__(self, canal):
        super().__init__(timeout=None)
        self.canal = canal

    async def _processar_fechamento(self, interaction, texto_inicio: str, *, forcar_log: bool = False, permitir_admin: bool = True):
        middle_id = ticket_middleman.get(self.canal.id)
        is_admin = interaction.user.guild_permissions.administrator

        autorizado = interaction.user.id == middle_id or (permitir_admin and is_admin)
        if not autorizado:
            msg_permissao = "Apenas o Middle que assumiu o ticket pode finalizar."
            if permitir_admin:
                msg_permissao = "Apenas o Middle que assumiu o ticket ou um administrador pode fechar."
            await interaction.response.send_message(
                msg_permissao,
                ephemeral=True, delete_after=60
            )
            return

        await interaction.response.send_message(
            texto_inicio,
            ephemeral=True, delete_after=60
        )

        canal_id = self.canal.id
        guild = self.canal.guild
        if forcar_log or deve_enviar_log_fechamento(canal_id):
            try:
                await enviar_log_fechamento_ticket(guild, self.canal)
            except Exception:
                logger.exception(
                    "Falha ao registrar fechamento de ticket canal_id=%s guild_id=%s",
                    canal_id,
                    guild.id if guild else "desconhecida"
                )
        else:
            logger.info(
                "Log de fechamento ignorado (ticket nao finalizado) canal_id=%s guild_id=%s tipo=%s",
                canal_id,
                guild.id if guild else "desconhecida",
                ticket_type.get(canal_id, "desconhecido")
            )

        # limpa dados do ticket
        remover_estado_ticket(canal_id)

        # deleta canal
        await self.canal.delete()

    @discord.ui.button(label="🔒 Fechar Ticket", style=ESTILO_BOTAO["perigo"])
    async def fechar(self, interaction, button):
        if await em_cooldown(interaction, "fechar_ticket", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return
        lock = await ticket_lock_or_wait_msg(interaction, self.canal.id)
        if lock is None:
            return
        async with lock:
            await self._processar_fechamento(interaction, "🔒 Fechando ticket...")

class FinalizarTicketView(FecharTicketView):
    def __init__(self, canal):
        super().__init__(canal)
        # Remove apenas o botão herdado de "Fechar Ticket" e mantém o botão de finalizar.
        self.remove_item(self.fechar)

    @discord.ui.button(label="✅ Finalizar Ticket", style=ESTILO_BOTAO["sucesso"])
    async def finalizar(self, interaction, button):
        if await em_cooldown(interaction, "finalizar_ticket", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return
        lock = await ticket_lock_or_wait_msg(interaction, self.canal.id)
        if lock is None:
            return
        async with lock:
            await self._processar_fechamento(interaction, "✅ Finalizando ticket...")

# ---------- NEGOCIAÇÃO INICIAL (PIX/BRAINROT) ----------
def iniciar_negociacao_ticket(canal_id, comprador, vendedor):
    ticket_negociacao[canal_id] = {
        "comprador_id": comprador.id if hasattr(comprador, "id") else _id_int(comprador),
        "vendedor_id": vendedor.id if hasattr(vendedor, "id") else _id_int(vendedor),
        "valor": None,
        "brainrot_nome": None,
        "confirm_msg_id": None,
        "etapa": "coleta_dados"
    }


def _estado_negociacao(canal_id):
    estado = ticket_negociacao.get(canal_id)
    if not isinstance(estado, dict):
        return None
    return estado


def deve_enviar_log_fechamento(canal_id):
    tipo = ticket_type.get(canal_id)
    estado = _estado_negociacao(canal_id) or {}

    if tipo in {"pix", "brainrot"}:
        return estado.get("etapa") == "finalizado"
    if tipo == "trade":
        return estado.get("trade_etapa") == "finalizado_trade"
    return False


def iniciar_negociacao_trade(canal_id, pessoa1=None, pessoa2=None):
    estado = _estado_negociacao(canal_id) or {}
    if pessoa1 is not None:
        estado["trade_pessoa1_id"] = pessoa1.id if hasattr(pessoa1, "id") else _id_int(pessoa1)
    if pessoa2 is not None:
        estado["trade_pessoa2_id"] = pessoa2.id if hasattr(pessoa2, "id") else _id_int(pessoa2)
    if not estado.get("trade_etapa"):
        estado["trade_etapa"] = "aguardando_parceiro_trade"
    ticket_negociacao[canal_id] = estado


async def tentar_publicar_confirmacao_negociacao(canal):
    estado = _estado_negociacao(canal.id)
    if not estado or estado.get("confirm_msg_id"):
        return
    if estado.get("etapa") not in {None, "coleta_dados", "aguardando_confirmacoes"}:
        return
    if estado.get("valor") is None or not estado.get("brainrot_nome"):
        return

    parties = obter_partes_ticket(canal)
    if not parties:
        await enviar_fluxo(
            canal,
            "Não foi possível recuperar comprador/vendedor deste ticket.",
            cor=cor_paleta("erro")
        )
        return

    comprador = parties["comprador"]
    vendedor = parties["vendedor"]
    valor = float(estado["valor"])
    brainrot_nome = estado["brainrot_nome"]
    ticket_kind = ticket_type.get(canal.id, "pix")
    taxa = 0 if ticket_kind == "brainrot" else calcular_taxa(valor, canal.guild.id)

    descricao = (
        f"**Brainrot informado por {vendedor.mention}:** `{brainrot_nome}`\n"
        f"**Valor informado por {comprador.mention}:** R$ {valor:.2f}\n"
    )
    if ticket_kind != "brainrot":
        descricao += (
            f"**Taxa estimada:** R$ {taxa:.2f}\n"
            f"**Total estimado:** R$ {valor + taxa:.2f}\n"
        )
    descricao += (
        f"\n{vendedor.mention}, confirme o valor.\n"
        f"{comprador.mention}, confirme o brainrot."
    )

    embed = discord.Embed(
        title="Confirmação da negociação",
        description=descricao,
        color=cor_paleta("destaque")
    )
    msg = await canal.send(
        embed=embed,
        view=ConfirmarNegociacaoView(canal, comprador, vendedor, valor)
    )
    estado["confirm_msg_id"] = msg.id
    estado["etapa"] = "aguardando_confirmacoes"


class ConfirmarNegociacaoView(discord.ui.View):
    def __init__(self, canal, comprador, vendedor, valor):
        super().__init__(timeout=None)
        self.canal = canal
        self.comprador = comprador
        self.vendedor = vendedor
        self.valor = valor
        self.valor_confirmado = False
        self.brainrot_confirmado = False
        self.mensagens_confirmacao_ids = []
        self.confirmar_valor.label = f"{self._nome_curto(self.vendedor)} confirma valor"
        self.confirmar_brainrot.label = f"{self._nome_curto(self.comprador)} confirma brainrot"

    def _nome_curto(self, membro, limite=18):
        nome = (membro.display_name or membro.name).strip()
        if len(nome) <= limite:
            return nome
        return nome[:limite - 3] + "..."

    async def _seguir_fluxo(self, interaction):
        if not (self.valor_confirmado and self.brainrot_confirmado):
            return
        estado = _estado_negociacao(self.canal.id)
        if estado and estado.get("etapa") not in {"aguardando_confirmacoes", "coleta_dados"}:
            return
        if estado:
            estado["etapa"] = "avanco_confirmacao_processando"
        for msg_id in list(self.mensagens_confirmacao_ids):
            try:
                msg = await self.canal.fetch_message(msg_id)
                await msg.delete()
            except discord.NotFound:
                pass
            except Exception:
                logger.exception(
                    "Falha ao apagar embed de confirmacao canal_id=%s msg_id=%s",
                    self.canal.id,
                    msg_id
                )
        try:
            await interaction.message.edit(view=None)
        except Exception:
            pass

        ticket_kind = ticket_type.get(self.canal.id, "pix")
        if ticket_kind == "brainrot":
            if estado:
                estado["etapa"] = "aguardando_taxa_brainrot_middle"
            await enviar_fluxo(
                self.canal,
                "Enviem o servidor/brainrot da taxa para o Middle.\n"
                "Quando receber, o Middle confirma abaixo:",
                view=ConfirmarTaxaBrainrotView(
                    self.canal,
                    self.valor,
                    self.comprador,
                    self.vendedor
                ),
                cor=cor_paleta("aviso")
            )
        else:
            if estado:
                estado["etapa"] = "aguardando_escolha_taxa"
            await enviar_fluxo(
                self.canal,
                f"💸 {self.comprador.mention}, informe quem irá pagar a taxa para o Middle Man.",
                view=TaxaView(self.valor, self.comprador, self.vendedor, self.canal.guild.id),
                cor=cor_paleta("aviso")
            )
        self.stop()

    @discord.ui.button(label="Comprador confirma valor", style=ESTILO_BOTAO["sucesso"])
    async def confirmar_valor(self, interaction, button):
        if await em_cooldown(interaction, "confirmar_valor", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return
        lock = await ticket_lock_or_wait_msg(interaction, self.canal.id)
        if lock is None:
            return
        async with lock:
            if interaction.user != self.vendedor:
                await interaction.response.send_message(
                    "Somente o vendedor confirma o valor.",
                    ephemeral=True, delete_after=60
                )
                return
            self.valor_confirmado = True
            button.disabled = True
            await interaction.response.defer()
            try:
                await interaction.message.edit(view=self)
            except Exception:
                pass
            embed = discord.Embed(
                description=f"{interaction.user.mention} confirmou a negociação.",
                color=cor_paleta("sucesso")
            )
            msg = await self.canal.send(embed=embed)
            self.mensagens_confirmacao_ids.append(msg.id)
            await self._seguir_fluxo(interaction)

    @discord.ui.button(label="Vendedor confirma brainrot", style=ESTILO_BOTAO["primario"])
    async def confirmar_brainrot(self, interaction, button):
        if await em_cooldown(interaction, "confirmar_brainrot", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return
        lock = await ticket_lock_or_wait_msg(interaction, self.canal.id)
        if lock is None:
            return
        async with lock:
            if interaction.user != self.comprador:
                await interaction.response.send_message(
                    "Somente o comprador confirma o brainrot.",
                    ephemeral=True, delete_after=60
                )
                return
            self.brainrot_confirmado = True
            button.disabled = True
            await interaction.response.defer()
            try:
                await interaction.message.edit(view=self)
            except Exception:
                pass
            embed = discord.Embed(
                description=f"{interaction.user.mention} confirmou a negociação.",
                color=cor_paleta("sucesso")
            )
            msg = await self.canal.send(embed=embed)
            self.mensagens_confirmacao_ids.append(msg.id)
            await self._seguir_fluxo(interaction)


class ValorModal(discord.ui.Modal, title="Valor da negociação"):
    valor = discord.ui.TextInput(label="Digite o valor")

    def __init__(self, canal, comprador, vendedor, origem_view=None):
        super().__init__()
        self.canal = canal
        self.comprador = comprador
        self.vendedor = vendedor
        self.origem_view = origem_view

    async def on_submit(self, interaction):
        try:
            valor = float(self.valor.value.replace(",", "."))
        except ValueError:
            await interaction.response.send_message("Valor inválido.", ephemeral=True, delete_after=60)
            return

        if valor <= 0:
            await interaction.response.send_message("Informe um valor maior que zero.", ephemeral=True, delete_after=60)
            return
        if valor > VALOR_MAXIMO_OPERACAO:
            await interaction.response.send_message(
                f"Valor muito alto. Limite permitido: R$ {VALOR_MAXIMO_OPERACAO:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                ephemeral=True, delete_after=60
            )
            return

        estado = _estado_negociacao(self.canal.id)
        if not estado:
            iniciar_negociacao_ticket(self.canal.id, self.comprador, self.vendedor)
            estado = _estado_negociacao(self.canal.id)
        if estado.get("etapa") not in {"coleta_dados", "aguardando_confirmacoes"}:
            await interaction.response.send_message(
                "Esta etapa já foi concluída. Siga o fluxo atual do ticket.",
                ephemeral=True,
                delete_after=60
            )
            return
        if estado.get("confirm_msg_id"):
            await interaction.response.send_message(
                "A confirmação da negociação já foi enviada. Use os botões de confirmação.",
                ephemeral=True,
                delete_after=60
            )
            return
        estado["valor"] = valor

        await enviar_fluxo(
            self.canal,
            f"✅ Valor registrado: R$ {valor:.2f}\n"
            f"Aguardando o vendedor informar o brainrot.",
            cor=cor_paleta("sucesso")
        )
        if self.origem_view is not None:
            await self.origem_view.marcar_valor_preenchido()
        await tentar_publicar_confirmacao_negociacao(self.canal)
        await interaction.response.send_message("Valor salvo.", ephemeral=True, delete_after=60)


class BrainrotNomeModal(discord.ui.Modal, title="Brainrot da negociação"):
    brainrot_nome = discord.ui.TextInput(label="Qual brainrot será vendido?", max_length=120)

    def __init__(self, canal, comprador, vendedor, origem_view=None):
        super().__init__()
        self.canal = canal
        self.comprador = comprador
        self.vendedor = vendedor
        self.origem_view = origem_view

    async def on_submit(self, interaction):
        nome = self.brainrot_nome.value.strip()
        if not nome:
            await interaction.response.send_message("Informe um nome de brainrot válido.", ephemeral=True, delete_after=60)
            return

        estado = _estado_negociacao(self.canal.id)
        if not estado:
            iniciar_negociacao_ticket(self.canal.id, self.comprador, self.vendedor)
            estado = _estado_negociacao(self.canal.id)
        if estado.get("etapa") not in {"coleta_dados", "aguardando_confirmacoes"}:
            await interaction.response.send_message(
                "Esta etapa já foi concluída. Siga o fluxo atual do ticket.",
                ephemeral=True,
                delete_after=60
            )
            return
        if estado.get("confirm_msg_id"):
            await interaction.response.send_message(
                "A confirmação da negociação já foi enviada. Use os botões de confirmação.",
                ephemeral=True,
                delete_after=60
            )
            return
        estado["brainrot_nome"] = nome

        await enviar_fluxo(
            self.canal,
            f"✅ Brainrot registrado: `{nome}`\n"
            f"Aguardando o comprador informar o valor.",
            cor=cor_paleta("sucesso")
        )
        if self.origem_view is not None:
            await self.origem_view.marcar_brainrot_preenchido()
        await tentar_publicar_confirmacao_negociacao(self.canal)
        await interaction.response.send_message("Brainrot salvo.", ephemeral=True, delete_after=60)


class NegociacaoDadosView(discord.ui.View):
    def __init__(self, canal, comprador, vendedor):
        super().__init__(timeout=None)
        self.canal = canal
        self.comprador = comprador
        self.vendedor = vendedor
        self.message = None
        self._valor_preenchido = False
        self._brainrot_preenchido = False
        self.informar_valor.label = f"{self._nome_curto(self.comprador)} Informe o valor"
        self.informar_brainrot.label = f"{self._nome_curto(self.vendedor)} Informe o brainrot"

    def _nome_curto(self, membro, limite=20):
        nome = (membro.display_name or membro.name).strip()
        if len(nome) <= limite:
            return nome
        return nome[:limite - 3] + "..."

    async def _atualizar_view(self):
        if self.message is None:
            return
        try:
            if len(self.children) == 0:
                await self.message.edit(view=None)
            else:
                await self.message.edit(view=self)
        except Exception:
            pass

    async def marcar_valor_preenchido(self):
        if self._valor_preenchido:
            return
        self._valor_preenchido = True
        self.remove_item(self.informar_valor)
        await self._atualizar_view()

    async def marcar_brainrot_preenchido(self):
        if self._brainrot_preenchido:
            return
        self._brainrot_preenchido = True
        self.remove_item(self.informar_brainrot)
        await self._atualizar_view()

    @discord.ui.button(label="Informar valor", style=ESTILO_BOTAO["primario"])
    async def informar_valor(self, interaction, button):
        if interaction.user != self.comprador:
            await interaction.response.send_message(
                "Somente o comprador pode informar o valor.",
                ephemeral=True, delete_after=60
            )
            return
        await interaction.response.send_modal(
            ValorModal(self.canal, self.comprador, self.vendedor, origem_view=self)
        )

    @discord.ui.button(label="Informar brainrot", style=ESTILO_BOTAO["primario"])
    async def informar_brainrot(self, interaction, button):
        if interaction.user != self.vendedor:
            await interaction.response.send_message(
                "Somente o vendedor pode informar o brainrot.",
                ephemeral=True, delete_after=60
            )
            return
        await interaction.response.send_modal(
            BrainrotNomeModal(self.canal, self.comprador, self.vendedor, origem_view=self)
        )


# ---------- MIDDLEMAN ACCEPT ----------
class IrParaTicketView(discord.ui.View):
    def __init__(self, canal):
        super().__init__(timeout=300)
        url = f"https://discord.com/channels/{canal.guild.id}/{canal.id}"
        self.add_item(discord.ui.Button(label="Ir para o ticket", style=discord.ButtonStyle.link, url=url))


class MiddlemanAcceptView(discord.ui.View):
    def __init__(self, canal, comprador, vendedor):
        super().__init__(timeout=None)
        self.canal = canal
        self.comprador = comprador
        self.vendedor = vendedor

    @discord.ui.button(label="Aceitar Ticket", style=ESTILO_BOTAO["sucesso"])
    async def aceitar(self, interaction, button):
        if await em_cooldown(interaction, "aceitar_ticket_middle", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return
        lock = await ticket_lock_or_wait_msg(interaction, self.canal.id)
        if lock is None:
            return
        async with lock:
            canal_existe = interaction.guild.get_channel(self.canal.id)
            if canal_existe is None:
                try:
                    await interaction.message.delete()
                except Exception:
                    pass
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Este ticket não existe mais. Mensagem de aceite removida.",
                        ephemeral=True, delete_after=60
                    )
                return

            role = get_middle_role(interaction.guild)

            if role not in interaction.user.roles:
                await interaction.response.send_message("Você não é MM.", ephemeral=True, delete_after=60)
                return

            middle_existente = ticket_middleman.get(self.canal.id)
            if middle_existente is not None:
                membro_existente = interaction.guild.get_member(int(middle_existente))
                if membro_existente:
                    await interaction.response.send_message(
                        f"Este ticket já foi aceito por {membro_existente.mention}.",
                        ephemeral=True,
                        delete_after=60
                    )
                else:
                    await interaction.response.send_message(
                        "Este ticket já foi aceito por outro Middle.",
                        ephemeral=True,
                        delete_after=60
                    )
                return

            # Reserva o ticket para este middle antes de qualquer await adicional.
            salvar_middleman_ticket(self.canal.id, interaction.user.id)
            await interaction.response.defer(ephemeral=True)

            await self.canal.set_permissions(interaction.user, view_channel=True)

            # remove mensagem de loading
            msg_loading = ticket_loading_msg.pop(self.canal.id, None)

            if msg_loading:
                try:
                    await msg_loading.delete()
                except:
                    pass

            embed_middle = discord.Embed(
                description=(
                    f"{interaction.user.mention} **aceitou o ticket e irá realizar o intermédio.**\n\n"
                    "Você será atendido por um dos membros da nossa equipe.\n"
                    "Caso tenha alguma dúvida sobre o ticket, pergunte ao Middle Man."
                ),
                color=cor_paleta("sucesso")
            )
            embed_middle.set_thumbnail(url=interaction.user.display_avatar.url)
            await self.canal.send(embed=embed_middle)

            await interaction.followup.send(
                "✅ Ticket assumido com sucesso.\nClique abaixo para abrir o ticket:",
                ephemeral=True,
                view=IrParaTicketView(self.canal)
            )
            try:
                await interaction.message.delete()
            except Exception:
                pass

            ticket_kind = ticket_type.get(self.canal.id, "pix")
            if ticket_kind in {"pix", "brainrot"}:
                partes = obter_partes_ticket(self.canal)
                if not partes:
                    estado = _estado_negociacao(self.canal.id)
                    if estado:
                        estado["etapa"] = "aguardando_middle_pix"
                    return

                comprador = partes["comprador"]
                vendedor = partes["vendedor"]
                estado = _estado_negociacao(self.canal.id)

                if not estado:
                    iniciar_negociacao_ticket(self.canal.id, comprador, vendedor)
                    estado = _estado_negociacao(self.canal.id)

                if estado.get("etapa") not in {"aguardando_middle_pix", "coleta_dados"} and estado.get("etapa") != "finalizado":
                    return
                if estado.get("confirm_msg_id"):
                    return

                if estado and estado.get("etapa") == "coleta_dados" and estado.get("valor") is not None:
                    return

                estado["etapa"] = "coleta_dados"
                await TicketView()._iniciar_fluxo_pix_brainrot(self.canal, comprador, vendedor)

# ---------- CONFIGURAÇÃO TRADE ----------
class TradeFinalConfirmView(discord.ui.View):
    def __init__(self, canal, pessoa1, pessoa2):
        super().__init__(timeout=None)
        self.canal = canal
        self.pessoa1 = pessoa1
        self.pessoa2 = pessoa2
        self.p1_ok = False
        self.p2_ok = False
        self.confirmar_p1.label = f"Confirmar: {self._nome_curto(self.pessoa1)}"
        self.confirmar_p2.label = f"Confirmar: {self._nome_curto(self.pessoa2)}"

    def _nome_curto(self, membro, limite=20):
        nome = (membro.display_name or membro.name).strip()
        if len(nome) <= limite:
            return nome
        return nome[:limite - 3] + "..."

    async def _verificar_finalizacao(self, interaction):
        if self.p1_ok and self.p2_ok:
            estado = _estado_negociacao(self.canal.id)
            if estado and estado.get("trade_etapa") not in {"aguardando_confirmacoes_trade", "finalizando_trade"}:
                return
            if estado:
                estado["trade_etapa"] = "finalizando_trade"
            try:
                await interaction.message.edit(view=None)
            except Exception:
                pass
            embed_finalizado = discord.Embed(
                title="Trade Finalizada",
                description=(
                    "✅ Trade finalizada com sucesso!\n\n"
                    "Obrigado por utilizar nosso sistema de middle man."
                ),
                color=cor_paleta("sucesso")
            )
            await self.canal.send(embed=embed_finalizado, view=FinalizarTicketView(self.canal))
            if estado:
                estado["trade_etapa"] = "finalizado_trade"

    @discord.ui.button(label="Pessoa 1 confirmou", style=ESTILO_BOTAO["primario"])
    async def confirmar_p1(self, interaction, button):
        if await em_cooldown(interaction, "trade_confirmar_p1", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return
        lock = await ticket_lock_or_wait_msg(interaction, self.canal.id)
        if lock is None:
            return
        async with lock:
            if interaction.user != self.pessoa1:
                await interaction.response.send_message("Apenas a pessoa 1 pode clicar aqui.", ephemeral=True, delete_after=60)
                return
            self.p1_ok = True
            await interaction.response.send_message("Confirmação recebida.", ephemeral=True, delete_after=60)
            await self._verificar_finalizacao(interaction)

    @discord.ui.button(label="Pessoa 2 confirmou", style=ESTILO_BOTAO["sucesso"])
    async def confirmar_p2(self, interaction, button):
        if await em_cooldown(interaction, "trade_confirmar_p2", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return
        lock = await ticket_lock_or_wait_msg(interaction, self.canal.id)
        if lock is None:
            return
        async with lock:
            if interaction.user != self.pessoa2:
                await interaction.response.send_message("Apenas a pessoa 2 pode clicar aqui.", ephemeral=True, delete_after=60)
                return
            self.p2_ok = True
            await interaction.response.send_message("Confirmação recebida.", ephemeral=True, delete_after=60)
            await self._verificar_finalizacao(interaction)


class ConfirmarPagamentoTradePixView(discord.ui.View):
    def __init__(self, canal, pessoa1, pessoa2, middle_id, pix_copia_cola=None):
        super().__init__(timeout=None)
        self.canal = canal
        self.pessoa1 = pessoa1
        self.pessoa2 = pessoa2
        self.middle_id = middle_id
        self.pix_copia_cola = pix_copia_cola
        if not self.pix_copia_cola:
            self.remove_item(self.copiar_codigo)

    @discord.ui.button(label="📋 Copiar código Pix", style=ESTILO_BOTAO["sucesso"], row=0)
    async def copiar_codigo(self, interaction, button):
        await interaction.response.send_message(
            f"`{self.pix_copia_cola}`",
            ephemeral=True
        )

    @discord.ui.button(label="Confirmar Pagamento ( MM )", style=ESTILO_BOTAO["aviso"], row=0)
    async def confirmar_pagamento(self, interaction, button):
        if await em_cooldown(interaction, "trade_confirmar_pagamento_pix", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return
        lock = await ticket_lock_or_wait_msg(interaction, self.canal.id)
        if lock is None:
            return
        async with lock:
            if interaction.user.id != self.middle_id:
                await interaction.response.send_message("Apenas o Middle pode confirmar o pagamento.", ephemeral=True, delete_after=60)
                return
            estado = _estado_negociacao(self.canal.id)
            if estado and estado.get("trade_etapa") != "aguardando_pagamento_pix_trade":
                await interaction.response.send_message(
                    "Esta etapa já foi processada.",
                    ephemeral=True,
                    delete_after=60
                )
                return
            if estado:
                estado["trade_etapa"] = "processando_pagamento_pix_trade"
            await interaction.response.defer()
            try:
                await interaction.message.delete()
            except discord.NotFound:
                pass
            if estado:
                estado["trade_etapa"] = "aguardando_confirmacoes_trade"
            await enviar_fluxo(
                self.canal,
                f"{self.pessoa1.mention} e {self.pessoa2.mention}, confirmem se a troca foi feita:",
                view=TradeFinalConfirmView(self.canal, self.pessoa1, self.pessoa2),
                cor=cor_paleta("destaque")
            )


class TradePixValorModal(discord.ui.Modal, title="Valor do PIX da Trade"):
    valor = discord.ui.TextInput(label="Digite o valor do Pix")

    def __init__(self, canal, pessoa1, pessoa2, middle_id):
        super().__init__()
        self.canal = canal
        self.pessoa1 = pessoa1
        self.pessoa2 = pessoa2
        self.middle_id = middle_id

    async def on_submit(self, interaction):
        try:
            valor = float(self.valor.value.replace(",", "."))
        except ValueError:
            await interaction.response.send_message("Valor inválido.", ephemeral=True, delete_after=60)
            return
        if valor <= 0:
            await interaction.response.send_message("Informe um valor maior que zero.", ephemeral=True, delete_after=60)
            return

        estado = _estado_negociacao(self.canal.id)
        if estado and estado.get("trade_etapa") != "aguardando_valor_pix_trade":
            await interaction.response.send_message(
                "Esta etapa já foi processada.",
                ephemeral=True, delete_after=60
            )
            return
        if estado:
            estado["trade_etapa"] = "processando_valor_pix_trade"

        ok, erro = await enviar_qr_fluxo_pix(
            self.canal,
            "trade_pix",
            {
                "middle_id": self.middle_id,
                "valor": valor,
                "pessoa1": self.pessoa1,
                "pessoa2": self.pessoa2
            }
        )
        if not ok:
            if estado:
                estado["trade_etapa"] = "aguardando_valor_pix_trade"
            await enviar_fluxo(
                self.canal,
                f"{erro}\nMiddle: use `/setpix` e clique no botão abaixo para tentar novamente.",
                view=ReenviarQrPixView(
                    self.canal,
                    "trade_pix",
                    {
                        "valor": valor,
                        "pessoa1": self.pessoa1,
                        "pessoa2": self.pessoa2
                    }
                ),
                cor=cor_paleta("erro")
            )
            await interaction.response.send_message(
                "Não foi possível gerar o QR agora. Configure o PIX e use o botão no ticket.",
                ephemeral=True,
                delete_after=60
            )
            return

        if estado:
            estado["trade_etapa"] = "aguardando_pagamento_pix_trade"
        await interaction.response.send_message("Cobrança enviada.", ephemeral=True, delete_after=60)


class TradePixValorView(discord.ui.View):
    def __init__(self, canal, pessoa1, pessoa2, middle_id):
        super().__init__(timeout=None)
        self.canal = canal
        self.pessoa1 = pessoa1
        self.pessoa2 = pessoa2
        self.middle_id = middle_id

    @discord.ui.button(label="Informar valor do PIX", style=ESTILO_BOTAO["sucesso"])
    async def informar_valor(self, interaction, button):
        if await em_cooldown(interaction, "trade_informar_valor_pix", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return

        if interaction.user.id != self.middle_id:
            await interaction.response.send_message("Apenas o Middle pode informar o valor.", ephemeral=True, delete_after=60)
            return
        estado = _estado_negociacao(self.canal.id)
        if estado and estado.get("trade_etapa") != "aguardando_valor_pix_trade":
            await interaction.response.send_message(
                "Esta etapa já foi processada.",
                ephemeral=True, delete_after=60
            )
            return
        await interaction.response.send_modal(
            TradePixValorModal(self.canal, self.pessoa1, self.pessoa2, self.middle_id)
        )


class TradeTaxaEscolhaView(discord.ui.View):
    def __init__(self, canal, pessoa1, pessoa2, middle_id):
        super().__init__(timeout=None)
        self.canal = canal
        self.pessoa1 = pessoa1
        self.pessoa2 = pessoa2
        self.middle_id = middle_id

    @discord.ui.button(label="Taxa Pix", style=ESTILO_BOTAO["sucesso"])
    async def taxa_pix(self, interaction, button):
        if await em_cooldown(interaction, "trade_escolher_taxa", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return

        if interaction.user.id != self.middle_id:
            await interaction.response.send_message("Apenas o Middle pode escolher a taxa.", ephemeral=True, delete_after=60)
            return
        estado = _estado_negociacao(self.canal.id)
        if estado and estado.get("trade_etapa") != "aguardando_escolha_taxa_trade":
            await interaction.response.send_message(
                "Esta etapa já foi processada.",
                ephemeral=True, delete_after=60
            )
            return
        if estado:
            estado["trade_etapa"] = "aguardando_valor_pix_trade"

        try:
            await interaction.message.edit(view=None)
        except Exception:
            pass
        await interaction.response.send_message(
            embed=embed_fluxo(
                "Middle, informe o valor da taxa em reais",
                cor=cor_paleta("aviso")
            ),
            view=TradePixValorView(self.canal, self.pessoa1, self.pessoa2, self.middle_id)
        )

    @discord.ui.button(label="Taxa Brainrot", style=ESTILO_BOTAO["primario"])
    async def taxa_brainrot(self, interaction, button):
        if await em_cooldown(interaction, "trade_escolher_taxa", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return

        if interaction.user.id != self.middle_id:
            await interaction.response.send_message("Apenas o Middle pode escolher a taxa.", ephemeral=True, delete_after=60)
            return
        estado = _estado_negociacao(self.canal.id)
        if estado and estado.get("trade_etapa") != "aguardando_escolha_taxa_trade":
            await interaction.response.send_message(
                "Esta etapa já foi processada.",
                ephemeral=True, delete_after=60
            )
            return
        if estado:
            estado["trade_etapa"] = "aguardando_confirmacao_taxa_brainrot_trade"

        try:
            await interaction.message.edit(view=None)
        except Exception:
            pass
        await interaction.response.send_message(
            embed=embed_fluxo(
                "O middle vai receber o Brainrot da taxa. Em seguida, a troca continuará.",
                cor=cor_paleta("aviso")
            ),
            view=ConfirmarTaxaTradeBrainrotView(self.canal, self.pessoa1, self.pessoa2, self.middle_id)
        )


class ConfirmarTaxaTradeBrainrotView(discord.ui.View):
    def __init__(self, canal, pessoa1, pessoa2, middle_id):
        super().__init__(timeout=None)
        self.canal = canal
        self.pessoa1 = pessoa1
        self.pessoa2 = pessoa2
        self.middle_id = middle_id

    @discord.ui.button(label="Recebi a taxa em Brainrot", style=ESTILO_BOTAO["sucesso"])
    async def confirmar_taxa(self, interaction, button):
        if await em_cooldown(interaction, "trade_confirmar_taxa_brainrot", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return
        lock = await ticket_lock_or_wait_msg(interaction, self.canal.id)
        if lock is None:
            return
        async with lock:
            if interaction.user.id != self.middle_id:
                await interaction.response.send_message(
                    "Apenas o Middle pode confirmar o recebimento da taxa.",
                    ephemeral=True, delete_after=60
                )
                return

            estado = _estado_negociacao(self.canal.id)
            if estado and estado.get("trade_etapa") != "aguardando_confirmacao_taxa_brainrot_trade":
                await interaction.response.send_message(
                    "Esta etapa já foi processada.",
                    ephemeral=True, delete_after=60
                )
                return
            if estado:
                estado["trade_etapa"] = "processando_taxa_brainrot_trade"

            await interaction.response.defer()
            try:
                await interaction.message.delete()
            except discord.NotFound:
                pass

            if estado:
                estado["trade_etapa"] = "aguardando_confirmacoes_trade"
            await enviar_fluxo(
                self.canal,
                f"{self.pessoa1.mention} e {self.pessoa2.mention}, confirmem se a troca foi feita:",
                view=TradeFinalConfirmView(self.canal, self.pessoa1, self.pessoa2),
                cor=cor_paleta("destaque")
            )


class MiddlemanAcceptTradeView(discord.ui.View):
    def __init__(self, canal, pessoa1, pessoa2):
        super().__init__(timeout=None)
        self.canal = canal
        self.pessoa1 = pessoa1
        self.pessoa2 = pessoa2

    @discord.ui.button(label="Aceitar Ticket", style=ESTILO_BOTAO["sucesso"])
    async def aceitar(self, interaction, button):
        if await em_cooldown(interaction, "aceitar_ticket_trade", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return
        lock = await ticket_lock_or_wait_msg(interaction, self.canal.id)
        if lock is None:
            return
        async with lock:
            canal_existe = interaction.guild.get_channel(self.canal.id)
            if canal_existe is None:
                try:
                    await interaction.message.delete()
                except Exception:
                    pass
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Este ticket não existe mais. Mensagem de aceite removida.",
                        ephemeral=True, delete_after=60
                    )
                return

            role = get_middle_role(interaction.guild)
            if role not in interaction.user.roles:
                await interaction.response.send_message("Você não é MM.", ephemeral=True, delete_after=60)
                return

            middle_existente = ticket_middleman.get(self.canal.id)
            if middle_existente is not None:
                membro_existente = interaction.guild.get_member(int(middle_existente))
                if membro_existente:
                    await interaction.response.send_message(
                        f"Este ticket já foi aceito por {membro_existente.mention}.",
                        ephemeral=True,
                        delete_after=60
                    )
                else:
                    await interaction.response.send_message(
                        "Este ticket já foi aceito por outro Middle.",
                        ephemeral=True,
                        delete_after=60
                    )
                return

            # Recarrega as partes em tempo real, pois o botão pode ter sido criado
            # antes da pessoa 2 ser adicionada ao ticket.
            partes_trade = obter_partes_trade(self.canal)
            if partes_trade:
                self.pessoa1 = partes_trade.get("pessoa1")
                self.pessoa2 = partes_trade.get("pessoa2")

            # Reserva o ticket para este middle antes de qualquer await adicional.
            salvar_middleman_ticket(self.canal.id, interaction.user.id)
            if not self.pessoa1 or not self.pessoa2:
                await interaction.response.defer(ephemeral=True)
                await self.canal.set_permissions(interaction.user, view_channel=True)
                msg_loading = ticket_loading_msg.pop(self.canal.id, None)
                if msg_loading:
                    try:
                        await msg_loading.delete()
                    except Exception:
                        pass

                embed_middle = discord.Embed(
                    description=(
                        f"{interaction.user.mention} **aceitou o ticket e irá realizar o intermédio.**\n\n"
                        "Você será atendido por um dos membros da nossa equipe.\n"
                        "Caso tenha alguma dúvida sobre o ticket, pergunte ao Middle Man."
                    ),
                    color=cor_paleta("sucesso")
                )
                embed_middle.set_thumbnail(url=interaction.user.display_avatar.url)
                await self.canal.send(embed=embed_middle)
                try:
                    await interaction.followup.send(
                        "✅ Ticket assumido com sucesso. Aguarde a escolha das partes para seguir com o fluxo.",
                        ephemeral=True,
                        view=IrParaTicketView(self.canal)
                    )
                except Exception:
                    pass
                try:
                    await interaction.message.delete()
                except Exception:
                    pass
                return

            await interaction.response.defer(ephemeral=True)
            await self.canal.set_permissions(interaction.user, view_channel=True)

            msg_loading = ticket_loading_msg.pop(self.canal.id, None)
            if msg_loading:
                try:
                    await msg_loading.delete()
                except Exception:
                    pass

            embed_middle = discord.Embed(
                description=(
                    f"{interaction.user.mention} **aceitou o ticket e irá realizar o intermédio.**\n\n"
                    "Você será atendido por um dos membros da nossa equipe.\n"
                    "Caso tenha alguma dúvida sobre o ticket, pergunte ao Middle Man."
                ),
                color=cor_paleta("sucesso")
            )
            embed_middle.set_thumbnail(url=interaction.user.display_avatar.url)
            await self.canal.send(embed=embed_middle)
            await interaction.followup.send(
                "✅ Ticket assumido com sucesso.\nClique abaixo para abrir o ticket:",
                ephemeral=True,
                view=IrParaTicketView(self.canal)
            )
            try:
                await interaction.message.delete()
            except Exception:
                pass

            estado = _estado_negociacao(self.canal.id)
            if estado and estado.get("trade_etapa") == "aguardando_middle_trade":
                estado["trade_etapa"] = "aguardando_escolha_taxa_trade"
                await enviar_fluxo(
                    self.canal,
                    "Qual a taxa da trade? (Pix ou Brainrot)",
                    view=TradeTaxaEscolhaView(self.canal, self.pessoa1, self.pessoa2, interaction.user.id),
                    cor=cor_paleta("aviso")
                )


class TradeSetupTradeView(discord.ui.View):
    def __init__(self, canal, criador):
        super().__init__(timeout=None)
        self.canal = canal
        self.criador = criador
        self.message = None
        self.escolha_feita = False

    @discord.ui.button(label="Adicionar pessoa da troca", style=ESTILO_BOTAO["primario"])
    async def adicionar(self, interaction, button):
        if await em_cooldown(interaction, "trade_adicionar_pessoa", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return

        if interaction.user != self.criador:
            await interaction.response.send_message("Somente quem abriu o ticket pode escolher.", ephemeral=True, delete_after=60)
            return
        if self.escolha_feita:
            await interaction.response.send_message("Pessoa já foi escolhida.", ephemeral=True, delete_after=60)
            return

        self.escolha_feita = True
        button.disabled = True
        select = discord.ui.UserSelect(placeholder="Escolher pessoa para trocar")
        select.callback = self.select_pessoa
        self.add_item(select)
        await interaction.response.send_message("Escolha a pessoa.", ephemeral=True, delete_after=60)
        await self.message.edit(view=self)

    async def select_pessoa(self, interaction):
        if interaction.user != self.criador:
            await interaction.response.send_message("Somente quem abriu o ticket pode escolher.", ephemeral=True, delete_after=60)
            return

        membro = interaction.guild.get_member(int(interaction.data["values"][0]))
        if membro is None or membro == self.criador:
            await interaction.response.send_message("Seleção inválida.", ephemeral=True, delete_after=60)
            return

        await self.canal.set_permissions(membro, view_channel=True)
        salvar_partes_trade(self.canal.id, self.criador, membro)
        iniciar_negociacao_trade(self.canal.id, self.criador, membro)
        estado = _estado_negociacao(self.canal.id)

        await self.message.edit(
            content=f"Pessoa 1: {self.criador.mention}\nPessoa 2: {membro.mention}",
            view=None
        )
        await interaction.response.send_message("Pessoa adicionada.", ephemeral=True, delete_after=60)

        middle_id = ticket_middleman.get(self.canal.id)
        if middle_id is not None:
            middle = self.canal.guild.get_member(int(middle_id))
            if middle is not None:
                await self.canal.set_permissions(middle, view_channel=True)
                if estado:
                    estado["trade_etapa"] = "aguardando_escolha_taxa_trade"
                await enviar_fluxo(
                    self.canal,
                    "Qual a taxa da trade? (Pix ou Brainrot)",
                    view=TradeTaxaEscolhaView(self.canal, self.criador, membro, middle.id),
                    cor=cor_paleta("aviso")
                )
                return
            ticket_middleman.pop(self.canal.id, None)
            salvar_estado_tickets()

        if estado:
            estado["trade_etapa"] = "aguardando_middle_trade"


class TradeSetupView(discord.ui.View):
    def __init__(self, canal, criador):
        super().__init__(timeout=None)
        self.canal = canal
        self.criador = criador
        self.message = None
        self.comprador = None
        self.vendedor = None
        self.escolha_feita = False

    async def _resolver_membro_selecionado(self, interaction: discord.Interaction):
        try:
            member_id = int(interaction.data["values"][0])
        except (TypeError, ValueError, KeyError, IndexError):
            return None

        membro = interaction.guild.get_member(member_id)
        if membro is not None:
            return membro

        try:
            return await interaction.guild.fetch_member(member_id)
        except Exception:
            return None

    async def _garantir_permissoes_partes(self):
        for membro in (self.comprador, self.vendedor):
            if membro is None:
                continue
            await self.canal.set_permissions(
                membro,
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )

    async def finalizar(self, interaction):
        await self._garantir_permissoes_partes()

        # salva partes do ticket
        salvar_partes_ticket(self.canal.id, self.comprador, self.vendedor)

        await self.message.edit(
            content=f"Comprador: {self.comprador.mention}\nVendedor: {self.vendedor.mention}",
            view=None
        )
        estado = _estado_negociacao(self.canal.id)
        if not estado:
            iniciar_negociacao_ticket(self.canal.id, self.comprador, self.vendedor)
            estado = _estado_negociacao(self.canal.id)

        if estado.get("confirm_msg_id"):
            return
        if estado.get("etapa") == "finalizado":
            return

        middle_id = ticket_middleman.get(self.canal.id)
        if middle_id is None:
            estado["etapa"] = "aguardando_middle_pix"
            return

        middle = self.canal.guild.get_member(int(middle_id))
        if middle is None:
            ticket_middleman.pop(self.canal.id, None)
            salvar_estado_tickets()
            if estado.get("etapa") not in {"aguardando_middle_pix", "coleta_dados"}:
                estado["etapa"] = "aguardando_middle_pix"
            await enviar_fluxo(
                self.canal,
                "⚠️ O Middle responsável não foi encontrado, aguardando novo aceite.",
                cor=cor_paleta("erro")
            )
            return

        if estado.get("etapa") == "coleta_dados" and estado.get("valor") is not None:
            return

        await self.canal.set_permissions(middle, view_channel=True)

        estado["etapa"] = "coleta_dados"
        view_dados = NegociacaoDadosView(self.canal, self.comprador, self.vendedor)
        msg_dados = await enviar_fluxo(
            self.canal,
            (
                f"{self.comprador.mention} **informe o valor da negociação.**\n"
                f"{self.vendedor.mention} **informe qual brainrot será negociado.**"
            ),
            view=view_dados,
            cor=cor_paleta("aviso")
        )
        view_dados.message = msg_dados

    # -------- botão comprador --------
    @discord.ui.button(label="Vou Pagar/Comprador", style=ESTILO_BOTAO["primario"])
    async def comprador_btn(self, interaction, button):
        if await em_cooldown(interaction, "definir_papel_trade", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return

        if interaction.user != self.criador:
            await interaction.response.send_message(
                "Somente quem abriu o ticket pode escolher.",
                ephemeral=True, delete_after=60
            )
            return

        if self.escolha_feita:
            await interaction.response.send_message(
                "Escolha já foi feita.",
                ephemeral=True, delete_after=60
            )
            return

        self.escolha_feita = True
        self.comprador = interaction.user

        # desativa botões
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

        select = discord.ui.UserSelect(
            placeholder="Escolher vendedor"
        )
        select.callback = self.select_vendedor
        self.add_item(select)

        await interaction.response.send_message(
            "Escolha vendedor.",
            ephemeral=True, delete_after=60
        )

        await self.message.edit(view=self)

    # -------- botão vendedor --------
    @discord.ui.button(label="Vou Receber/Vendedor", style=ESTILO_BOTAO["sucesso"])
    async def vendedor_btn(self, interaction, button):
        if await em_cooldown(interaction, "definir_papel_trade", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return

        if interaction.user != self.criador:
            await interaction.response.send_message(
                "Somente quem abriu o ticket pode escolher.",
                ephemeral=True, delete_after=60
            )
            return

        if self.escolha_feita:
            await interaction.response.send_message(
                "Escolha já foi feita.",
                ephemeral=True, delete_after=60
            )
            return

        self.escolha_feita = True
        self.vendedor = interaction.user

        # desativa botões
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

        select = discord.ui.UserSelect(
            placeholder="Escolher comprador"
        )
        select.callback = self.select_comprador
        self.add_item(select)

        await interaction.response.send_message(
            "Escolha comprador.",
            ephemeral=True, delete_after=60
        )

        await self.message.edit(view=self)

    # -------- seleção vendedor --------
    async def select_vendedor(self, interaction):
        membro = await self._resolver_membro_selecionado(interaction)
        if membro is None:
            await interaction.response.send_message(
                "Não consegui localizar esse membro no servidor.",
                ephemeral=True, delete_after=60
            )
            return

        self.vendedor = membro

        await self.canal.set_permissions(
            membro,
            view_channel=True,
            send_messages=True,
            read_message_history=True
        )

        await interaction.response.send_message(
            "Vendedor definido.",
            ephemeral=True, delete_after=60
        )

        await self.finalizar(interaction)

    # -------- seleção comprador --------
    async def select_comprador(self, interaction):
        membro = await self._resolver_membro_selecionado(interaction)
        if membro is None:
            await interaction.response.send_message(
                "Não consegui localizar esse membro no servidor.",
                ephemeral=True, delete_after=60
            )
            return

        self.comprador = membro

        await self.canal.set_permissions(
            membro,
            view_channel=True,
            send_messages=True,
            read_message_history=True
        )

        await interaction.response.send_message(
            "Comprador definido.",
            ephemeral=True, delete_after=60
        )

        await self.finalizar(interaction)


# ---------- TICKET BUTTON ----------
class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _enviar_confirmacao_abertura(self, interaction, canal, descricao):
        embed = discord.Embed(
            description=descricao,
            color=cor_paleta("sucesso")
        )

        link_view = discord.ui.View()
        link_view.add_item(
            discord.ui.Button(
                label="Ir para o ticket",
                url=canal.jump_url
            )
        )

        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    embed=embed,
                    view=link_view,
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    embed=embed,
                    view=link_view,
                    ephemeral=True
                )
        except Exception:
            logger.exception(
                "Falha ao enviar confirmacao de abertura de ticket canal_id=%s user_id=%s",
                canal.id,
                interaction.user.id
            )
            try:
                await interaction.user.send(embed=embed)
            except Exception:
                pass

    async def _avisar_aceite_pix_brainrot(self, canal, comprador, vendedor):
        aceite_canal_id = get_aceite_canal_id(canal.guild.id)
        if not aceite_canal_id:
            await enviar_fluxo(
                canal,
                "⚠️ Canal de aceite não configurado. Um administrador deve usar `/setaceite`.",
                cor=cor_paleta("erro")
            )
            return

        try:
            channel_id = int(aceite_canal_id)
        except (TypeError, ValueError):
            channel_id = None

        aceite_channel = None
        if channel_id is not None:
            aceite_channel = canal.guild.get_channel(channel_id)
            if aceite_channel is None:
                try:
                    aceite_channel = await canal.guild.fetch_channel(channel_id)
                except (discord.HTTPException, discord.NotFound, discord.Forbidden):
                    aceite_channel = None

        if not isinstance(aceite_channel, discord.TextChannel):
            await enviar_fluxo(
                canal,
                "⚠️ Canal de aceite não configurado. Um administrador deve usar `/setaceite`.",
                cor=cor_paleta("erro")
            )
            return

        ticket_kind = ticket_type.get(canal.id, "pix")
        tipo_middle = "Taxa Brain Rot" if ticket_kind == "brainrot" else "Taxa Pix"
        role_middle = get_middle_role(canal.guild)
        mencao_middle = role_middle.mention if role_middle else "@Middle Man"
        try:
            await aceite_channel.send(
                content=mencao_middle,
                embed=embed_fluxo(
                    f"Ticket aguardando MM ({tipo_middle}): {canal.mention}",
                    cor=cor_paleta("aviso")
                ),
                view=MiddlemanAcceptView(canal, comprador, vendedor)
            )
        except discord.Forbidden:
            await enviar_fluxo(
                canal,
                "⚠️ Sem permissão para enviar no canal de aceite configurado. "
                "Verifique se o bot tem permissão de envio de mensagens.",
                cor=cor_paleta("erro")
            )

    async def _avisar_aceite_trade(self, canal, pessoa1, pessoa2):
        aceite_canal_id = get_aceite_canal_id(canal.guild.id)
        if not aceite_canal_id:
            await enviar_fluxo(
                canal,
                "⚠️ Canal de aceite não configurado. Um administrador deve usar `/setaceite`.",
                cor=cor_paleta("erro")
            )
            return

        try:
            channel_id = int(aceite_canal_id)
        except (TypeError, ValueError):
            channel_id = None

        aceite_channel = None
        if channel_id is not None:
            aceite_channel = canal.guild.get_channel(channel_id)
            if aceite_channel is None:
                try:
                    aceite_channel = await canal.guild.fetch_channel(channel_id)
                except (discord.HTTPException, discord.NotFound, discord.Forbidden):
                    aceite_channel = None

        if not isinstance(aceite_channel, discord.TextChannel):
            await enviar_fluxo(
                canal,
                "⚠️ Canal de aceite não configurado. Um administrador deve usar `/setaceite`.",
                cor=cor_paleta("erro")
            )
            return

        role_middle = get_middle_role(canal.guild)
        mencao_middle = role_middle.mention if role_middle else "@Middle Man"
        try:
            await aceite_channel.send(
                content=mencao_middle,
                embed=embed_fluxo(
                    f"Ticket aguardando MM (Trade): {canal.mention}",
                    cor=cor_paleta("aviso")
                ),
                view=MiddlemanAcceptTradeView(canal, pessoa1, pessoa2)
            )
        except discord.Forbidden:
            await enviar_fluxo(
                canal,
                "⚠️ Sem permissão para enviar no canal de aceite configurado. "
                "Verifique se o bot tem permissão de envio de mensagens.",
                cor=cor_paleta("erro")
            )

    def proximo_numero_ticket_pix(self, guild):
        guild_id = guild.id

        pattern = re.compile(r"^🔃-ticket-(\d+)$")
        maior_existente = 0

        for canal in guild.text_channels:
            match = pattern.match(canal.name)
            if not match:
                continue
            numero = _id_int(match.group(1))
            if numero and numero > maior_existente:
                maior_existente = numero

        atual = _id_int(ticket_count_by_guild.get(guild_id))
        if atual is None or atual < 0:
            # Compatibilidade com estado antigo (contador global).
            atual = _id_int(ticket_count_by_guild.get("_legacy_global")) or 0

        ticket_count_by_guild[guild_id] = max(atual, maior_existente) + 1
        ticket_count_by_guild.pop("_legacy_global", None)
        salvar_estado_tickets()
        return ticket_count_by_guild[guild_id]

    def proximo_nome_ticket(self, guild, prefixo):
        pattern = re.compile(rf"^{re.escape(prefixo)}(?:-(\d+))?$")
        total = 0

        for canal in guild.text_channels:
            if pattern.match(canal.name):
                total += 1

        return f"{prefixo}-{total + 1}"

    def contar_tickets_abertos_por_criador(self, guild, user_id):
        uid = _id_int(user_id)
        if uid is None:
            return 0
        total = 0
        for canal_id, criador_id in ticket_creator.items():
            if criador_id != uid:
                continue
            if canal_id not in ticket_type:
                continue
            canal = guild.get_channel(canal_id)
            if isinstance(canal, discord.TextChannel):
                total += 1
        return total

    async def obter_ou_criar_categoria_middle(self, guild):
        category_id = get_middle_category_id(guild.id)
        if category_id is not None:
            categoria_cfg = guild.get_channel(category_id)
            if isinstance(categoria_cfg, discord.CategoryChannel):
                return categoria_cfg

        nome_categoria = "middle man"
        for categoria in guild.categories:
            if categoria.name.strip().lower() == nome_categoria:
                return categoria

        return await guild.create_category(nome_categoria)

    async def _avisar_middles_no_canal(self, canal, comprador=None, vendedor=None, ticket_kind="pix"):
        embed = discord.Embed(
            title="⏳ Aguardando Middle Man",
            description="🔄 Um Middle irá aceitar o ticket em breve...",
            color=cor_paleta("aviso")
        )
        msg_loading = await canal.send(embed=embed)
        ticket_loading_msg[canal.id] = msg_loading

        if ticket_kind == "trade":
            await self._avisar_aceite_trade(canal, comprador, vendedor)
            return

        await self._avisar_aceite_pix_brainrot(canal, comprador, vendedor)

    async def _iniciar_fluxo_pix_brainrot(self, canal, comprador, vendedor, reiniciado=False):
        iniciar_negociacao_ticket(canal.id, comprador, vendedor)
        estado = _estado_negociacao(canal.id)

        if estado and estado.get("etapa") == "finalizado":
            return

        if estado:
            estado["etapa"] = "coleta_dados"
            estado["confirm_msg_id"] = None

        prefixo = "🔄 Bot reiniciado. " if reiniciado else ""
        view_dados = NegociacaoDadosView(canal, comprador, vendedor)
        msg_dados = await enviar_fluxo(
            canal,
            (
                f"{prefixo}{comprador.mention} **informe o valor da negociação.**\n"
                f"{vendedor.mention} **informe qual brainrot será negociado.**"
            ),
            view=view_dados,
            cor=cor_paleta("aviso")
        )
        view_dados.message = msg_dados

    async def criar_ticket_middleman_pix(self, interaction):
        if self.contar_tickets_abertos_por_criador(interaction.guild, interaction.user.id) >= 2:
            if interaction.response.is_done():
                await interaction.followup.send(
                    "Você já tem 2 tickets abertos. Feche um ticket antes de abrir outro.",
                    ephemeral=True, delete_after=60
                )
            else:
                await interaction.response.send_message(
                    "Você já tem 2 tickets abertos. Feche um ticket antes de abrir outro.",
                    ephemeral=True, delete_after=60
                )
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        numero_ticket = self.proximo_numero_ticket_pix(interaction.guild)

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True)
        }

        canal = await interaction.guild.create_text_channel(
            name=f"🔃-ticket-{numero_ticket}",
            overwrites=overwrites,
            category=await self.obter_ou_criar_categoria_middle(interaction.guild)
        )
        salvar_tipo_ticket(canal.id, "pix")
        salvar_criador_ticket(canal.id, interaction.user.id)
        aviso = discord.Embed(
            title="📢 LEIA COM ATENÇÃO",
            description=(
                "A taxa de middleman não é reembolsável.\n\n"
                "Ao assumir um ticket, o middle reserva tempo, disponibilidade e responsabilidade exclusiva para aquela negociação, deixando de atender outros atendimentos. Dessa forma, o serviço é considerado iniciado no momento da designação do middle, independentemente da conclusão da trade.\n\n"
                "Em caso de desistência de qualquer das partes após o pagamento, não há reembolso da taxa, conforme regras do servidor e os princípios da prestação de serviços e da boa-fé objetiva previstos no Código Civil Brasileiro.\n\n"
                "Ao efetuar o pagamento, o usuário declara estar ciente e de acordo com essa política.\n\n"
                f"Obrigado.{interaction.user.mention}"
            ),
            color=cor_paleta("info")
        )
        await enviar_fluxo(
            canal,
            f"👋 {interaction.user.mention} **seu ticket foi aberto com sucesso!**\n\n"
            "*Responda as perguntas para continuar o atendimento.*",
            cor=cor_paleta("sucesso")
        )

        await canal.send(
            embed=aviso,
            view=FecharTicketView(canal)
        )
        aviso_comprovacao = discord.Embed(
            description=(
                "**Solicitamos que toda a negociação, bem como a entrega e o recebimento do produto, "
                "sejam devidamente gravados ou registrados por meio de prints.**\n\n"
                "Em casos de denúncia por não recebimento, **poderão ser solicitadas provas que comprovem "
                "a transação e a entrega.**\n\n"
                "Ressaltamos que, **na ausência dessas comprovações**, o julgamento poderá ser considerado "
                "insatisfatório para uma das partes."
            ),
            color=cor_paleta("erro")
        )
        await canal.send(embed=aviso_comprovacao)

        await self._avisar_middles_no_canal(canal, ticket_kind="pix")
        view = TradeSetupView(canal, interaction.user)
        msg = await enviar_fluxo(
            canal,
            "Você vai **PAGAR** ou **RECEBER** o dinheiro",
            view=view,
            cor=cor_paleta("primario")
        )
        view.message = msg

        await self._enviar_confirmacao_abertura(
            interaction,
            canal,
            f"✅ | {interaction.user.mention}, seu ticket foi aberto!\n"
            "Clique abaixo para encontrá-lo."
        )

    async def criar_ticket_middleman_brainrot(self, interaction):
        if self.contar_tickets_abertos_por_criador(interaction.guild, interaction.user.id) >= 2:
            if interaction.response.is_done():
                await interaction.followup.send(
                    "Você já tem 2 tickets abertos. Feche um ticket antes de abrir outro.",
                    ephemeral=True, delete_after=60
                )
            else:
                await interaction.response.send_message(
                    "Você já tem 2 tickets abertos. Feche um ticket antes de abrir outro.",
                    ephemeral=True, delete_after=60
                )
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        nome_canal = self.proximo_nome_ticket(interaction.guild, "💠-ticket-middle-brainrot")
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True)
        }

        canal = await interaction.guild.create_text_channel(
            name=nome_canal,
            overwrites=overwrites,
            category=await self.obter_ou_criar_categoria_middle(interaction.guild)
        )
        salvar_tipo_ticket(canal.id, "brainrot")
        salvar_criador_ticket(canal.id, interaction.user.id)

        aviso = discord.Embed(
            title="📢 LEIA COM ATENÇÃO",
            description=(
                "A taxa de middleman não é reembolsável.\n\n"
                "Ao assumir um ticket, o middle reserva tempo, disponibilidade e responsabilidade exclusiva para aquela negociação, deixando de atender outros atendimentos. Dessa forma, o serviço é considerado iniciado no momento da designação do middle, independentemente da conclusão da trade.\n\n"
                "Em caso de desistência de qualquer das partes após o pagamento, não há reembolso da taxa, conforme regras do servidor e os princípios da prestação de serviços e da boa-fé objetiva previstos no Código Civil Brasileiro.\n\n"
                "Ao efetuar o pagamento, o usuário declara estar ciente e de acordo com essa política.\n\n"
                f"Obrigado.{interaction.user.mention}"
            ),
            color=cor_paleta("info")
        )

        await enviar_fluxo(
            canal,
            f"👋 {interaction.user.mention} **seu ticket foi aberto com sucesso!**\n\n"
            "*Responda as perguntas para continuar o atendimento.*",
            cor=cor_paleta("sucesso")
        )

        await canal.send(
            embed=aviso,
            view=FecharTicketView(canal)
        )
        aviso_comprovacao = discord.Embed(
            description=(
                "Solicitamos que toda a negociação, bem como a entrega e o recebimento do produto, "
                "sejam devidamente gravados ou registrados por meio de prints.\n\n"
                "Em casos de denúncia por não recebimento, poderão ser solicitadas provas que comprovem "
                "a transação e a entrega.\n\n"
                "Ressaltamos que, na ausência dessas comprovações, o julgamento poderá ser considerado "
                "insatisfatório para uma das partes."
            ),
            color=cor_paleta("erro")
        )
        await canal.send(embed=aviso_comprovacao)

        await self._avisar_middles_no_canal(canal, ticket_kind="brainrot")
        view = TradeSetupView(canal, interaction.user)
        msg = await enviar_fluxo(
            canal,
            "Você é comprador ou vendedor?",
            view=view,
            cor=cor_paleta("primario")
        )
        view.message = msg

        await self._enviar_confirmacao_abertura(
            interaction,
            canal,
            f"✅ | {interaction.user.mention}, seu ticket foi aberto!\n"
            "Clique abaixo para encontrá-lo."
        )

    async def criar_ticket_middleman_trade(self, interaction):
        if self.contar_tickets_abertos_por_criador(interaction.guild, interaction.user.id) >= 2:
            if interaction.response.is_done():
                await interaction.followup.send(
                    "Você já tem 2 tickets abertos. Feche um ticket antes de abrir outro.",
                    ephemeral=True, delete_after=60
                )
            else:
                await interaction.response.send_message(
                    "Você já tem 2 tickets abertos. Feche um ticket antes de abrir outro.",
                    ephemeral=True, delete_after=60
                )
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        nome_canal = self.proximo_nome_ticket(interaction.guild, "💱-ticket-middle-trade")
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True)
        }

        canal = await interaction.guild.create_text_channel(
            name=nome_canal,
            overwrites=overwrites,
            category=await self.obter_ou_criar_categoria_middle(interaction.guild)
        )
        salvar_tipo_ticket(canal.id, "trade")
        salvar_criador_ticket(canal.id, interaction.user.id)

        aviso = discord.Embed(
            title="📢 LEIA COM ATENÇÃO",
            description=(
                "A taxa de middleman não é reembolsável.\n\n"
                "Ao assumir um ticket, o middle reserva tempo, disponibilidade e responsabilidade exclusiva para aquela negociação, deixando de atender outros atendimentos. Dessa forma, o serviço é considerado iniciado no momento da designação do middle, independentemente da conclusão da trade.\n\n"
                "Em caso de desistência de qualquer das partes após o pagamento, não há reembolso da taxa, conforme regras do servidor e os princípios da prestação de serviços e da boa-fé objetiva previstos no Código Civil Brasileiro.\n\n"
                "Ao efetuar o pagamento, o usuário declara estar ciente e de acordo com essa política.\n\n"
                f"Obrigado.{interaction.user.mention}"
            ),
            color=cor_paleta("info")
        )

        await enviar_fluxo(
            canal,
            f"👋 {interaction.user.mention} **seu ticket foi aberto com sucesso!**\n\n"
            "*Responda as perguntas para continuar o atendimento.*",
            cor=cor_paleta("sucesso")
        )

        await canal.send(
            embed=aviso,
            view=FecharTicketView(canal)
        )
        aviso_comprovacao = discord.Embed(
            description=(
                "Solicitamos que toda a negociação, bem como a entrega e o recebimento do produto, "
                "sejam devidamente gravados ou registrados por meio de prints.\n\n"
                "Em casos de denúncia por não recebimento, poderão ser solicitadas provas que comprovem "
                "a transação e a entrega.\n\n"
                "Ressaltamos que, na ausência dessas comprovações, o julgamento poderá ser considerado "
                "insatisfatório para uma das partes."
            ),
            color=cor_paleta("erro")
        )
        await canal.send(embed=aviso_comprovacao)

        view_trade = TradeSetupTradeView(canal, interaction.user)
        msg = await enviar_fluxo(
            canal,
            "Com quem você vai trocar?",
            view=view_trade,
            cor=cor_paleta("primario")
        )
        view_trade.message = msg
        await self._avisar_middles_no_canal(canal, ticket_kind="trade")

        await self._enviar_confirmacao_abertura(
            interaction,
            canal,
            f"✅ | {interaction.user.mention}, seu ticket de trade foi aberto!\n"
            "Clique abaixo para encontrá-lo."
        )

    class EscolhaTaxaMiddleView(discord.ui.View):
        def __init__(self, ticket_view):
            super().__init__(timeout=120)
            self.ticket_view = ticket_view

        @discord.ui.button(label="Venda/Compra | Taxa Pix", style=ESTILO_BOTAO["sucesso"])
        async def taxa_pix(self, interaction, button):
            if await em_cooldown(interaction, "abrir_ticket_middle", COOLDOWN_ABRIR_TICKET_SEGUNDOS):
                return
            await self.ticket_view.criar_ticket_middleman_pix(interaction)

        @discord.ui.button(label="Venda/Compra | Taxa Brainrot", style=ESTILO_BOTAO["primario"])
        async def taxa_brainrot(self, interaction, button):
            if await em_cooldown(interaction, "abrir_ticket_middle", COOLDOWN_ABRIR_TICKET_SEGUNDOS):
                return
            await self.ticket_view.criar_ticket_middleman_brainrot(interaction)

        @discord.ui.button(label="Trade", style=ESTILO_BOTAO["perigo"])
        async def trade(self, interaction, button):
            if await em_cooldown(interaction, "abrir_ticket_middle", COOLDOWN_ABRIR_TICKET_SEGUNDOS):
                return
            await self.ticket_view.criar_ticket_middleman_trade(interaction)

    @discord.ui.button(label="💠Solicitar Middle Man", style=ESTILO_BOTAO["sucesso"])
    async def abrir(self, interaction, button):
        if await em_cooldown(interaction, "abrir_menu_ticket_middle", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return
        await interaction.response.send_message(
            "Escolha o tipo de Middle Man para solicitar:",
            view=self.EscolhaTaxaMiddleView(self),
            ephemeral=True, delete_after=60
        )


bot = botd()

@bot.tree.command(name="setpix", description="Define sua chave Pix e nome")
async def setpix(interaction: discord.Interaction, chave: str, nome: str):
    role = get_middle_role(interaction.guild)
    if role not in interaction.user.roles:
        await interaction.response.send_message(
            "Apenas quem tem o cargo de Middle configurado pode usar este comando.",
            ephemeral=True, delete_after=60
        )
        return

    if not validar_chave_pix(chave):
        await interaction.response.send_message(
            "❌ Chave PIX inválida. Use CPF, CNPJ, e-mail, telefone (+55...) ou chave aleatória válida.",
            ephemeral=True, delete_after=60
        )
        return

    set_pix(interaction.user.id, chave, nome)

    await interaction.response.send_message(
        f"✅ Chave Pix e nome atualizados {interaction.user.mention}",
        ephemeral=True, delete_after=60
    )

@bot.tree.command(name="painel1", description="Enviar painel de tickets")
async def painel1(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "Apenas administradores podem usar este comando.",
            ephemeral=True, delete_after=60
        )
        return

    await interaction.response.send_message(
        embed=criar_embed_painel(interaction.guild.id if interaction.guild else None),
        view=TicketView()
    )

@bot.tree.command(name="setpainel", description="Define o canal fixo do painel")
async def setpainel(interaction: discord.Interaction, canal: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "Apenas administradores podem usar este comando.",
            ephemeral=True, delete_after=60
        )
        return

    set_painel_canal(interaction.guild.id, canal.id)

    try:
        await canal.send(embed=criar_embed_painel(interaction.guild.id), view=TicketView())
    except discord.Forbidden:
        await interaction.response.send_message(
            "Não tenho permissão para enviar mensagem nesse canal.",
            ephemeral=True, delete_after=60
        )
        return

    await interaction.response.send_message(
        f"✅ Painel configurado com sucesso em {canal.mention}.",
        ephemeral=True, delete_after=60
    )

@bot.tree.command(name="setimgp", description="Define a imagem do painel por URL")
async def setimgp(interaction: discord.Interaction, url: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "Apenas administradores podem usar este comando.",
            ephemeral=True, delete_after=60
        )
        return

    if not validar_url_imagem(url):
        await interaction.response.send_message(
            "URL inválida. Envie uma URL iniciando com http:// ou https://",
            ephemeral=True, delete_after=60
        )
        return

    guild_id = interaction.guild.id
    set_painel_image_url(guild_id, url.strip())

    painel_canal_id = get_painel_canal_id(guild_id)
    canal = None
    if painel_canal_id is not None:
        try:
            canal = interaction.guild.get_channel(painel_canal_id)
            if canal is None:
                canal = await interaction.guild.fetch_channel(painel_canal_id)
        except Exception:
            canal = None

    if isinstance(canal, discord.TextChannel):
        try:
            await canal.purge(limit=50, check=lambda m: m.author == bot.user)
            await canal.send(embed=criar_embed_painel(guild_id), view=TicketView())
        except discord.Forbidden:
            await interaction.response.send_message(
                "Imagem salva, mas não tenho permissão para atualizar o painel no canal configurado.",
                ephemeral=True, delete_after=60
            )
            return
        except discord.HTTPException:
            await interaction.response.send_message(
                "Imagem salva, mas falhou ao atualizar o painel agora.",
                ephemeral=True, delete_after=60
            )
            return

    await interaction.response.send_message(
        "✅ Imagem do painel atualizada para este servidor.",
        ephemeral=True, delete_after=60
    )

@bot.tree.command(name="setaceite", description="Define o canal de pedidos para os middleman")
async def setaceite(interaction: discord.Interaction, canal: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "Apenas administradores podem usar este comando.",
            ephemeral=True, delete_after=60
        )
        return

    set_aceite_canal(interaction.guild.id, canal.id)

    await interaction.response.send_message(
        f"✅ Canal de aceite configurado com sucesso em {canal.mention}.",
        ephemeral=True, delete_after=60
    )


@bot.tree.command(name="setrolemiddle", description="Define qual cargo pode atuar como Middle")
async def setrolemiddle(interaction: discord.Interaction, cargo: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "Apenas administradores podem usar este comando.",
            ephemeral=True, delete_after=60
        )
        return

    set_middle_role_id(interaction.guild.id, cargo.id)
    await interaction.response.send_message(
        f"✅ Cargo de Middle configurado para {cargo.mention}.",
        ephemeral=True, delete_after=60
    )


@bot.tree.command(name="setcmiddle", description="Define a categoria onde os tickets de middle serão criados")
async def setcmiddle(interaction: discord.Interaction, categoria: discord.CategoryChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "Apenas administradores podem usar este comando.",
            ephemeral=True, delete_after=60
        )
        return

    set_middle_category_id(interaction.guild.id, categoria.id)
    await interaction.response.send_message(
        f"✅ Categoria de tickets configurada para **{categoria.name}**.",
        ephemeral=True, delete_after=60
    )


@bot.tree.command(name="setnvl", description="Configura cargo de nível por valor gasto")
@app_commands.describe(
    cargo="Cargo que será concedido no nível",
    valor="Valor mínimo acumulado para receber o cargo"
)
async def setnvl(interaction: discord.Interaction, cargo: discord.Role, valor: float):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "Apenas administradores podem usar este comando.",
            ephemeral=True, delete_after=60
        )
        return

    if valor < 0:
        await interaction.response.send_message(
            "O valor mínimo não pode ser negativo.",
            ephemeral=True, delete_after=60
        )
        return

    set_level_guild(interaction.guild.id, cargo.id, valor)
    niveis = get_levels_guild(interaction.guild.id)

    linhas = []
    for nivel in niveis:
        role = interaction.guild.get_role(nivel["role_id"])
        role_txt = role.mention if role else f"`{nivel['role_id']}`"
        linhas.append(f"- {role_txt}: R$ {nivel['min_total']:.2f}")
    resumo = "\n".join(linhas) if linhas else "Nenhum nível configurado."

    await interaction.response.send_message(
        f"✅ Nível atualizado: {cargo.mention} em **R$ {valor:.2f}**.\n\n**Níveis deste servidor:**\n{resumo}",
        ephemeral=True, delete_after=60
    )


@bot.tree.command(name="setlogs", description="Define o canal de logs do bot")
async def setlogs(interaction: discord.Interaction, canal: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "Apenas administradores podem usar este comando.",
            ephemeral=True, delete_after=60
        )
        return

    set_logs_canal(interaction.guild.id, canal.id)
    logger.info(
        "Canal de logs configurado guild_id=%s canal_id=%s por user_id=%s",
        interaction.guild.id,
        canal.id,
        interaction.user.id
    )


@bot.tree.command(name="setlogadmin", description="Define o canal de logs administrativos (transcrições)")
async def setlogadmin(interaction: discord.Interaction, canal: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "Apenas administradores podem usar este comando.",
            ephemeral=True, delete_after=60
        )
        return

    set_log_admin_canal(interaction.guild.id, canal.id)
    await interaction.response.send_message(
        f"✅ Canal de log administrativo configurado em {canal.mention}.",
        ephemeral=True, delete_after=60
    )


def _fmt_canal_configurado(guild: discord.Guild, channel_id):
    cid = _id_int(channel_id)
    if cid is None:
        return "Não configurado"
    canal = guild.get_channel(cid)
    if canal is None:
        return f"ID `{cid}` (não encontrado)"
    return f"{canal.mention} (`{cid}`)"


@bot.tree.command(name="infocanal", description="Mostra os canais configurados pelos comandos /set")
async def infocanal(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message(
            "Este comando só pode ser usado dentro de um servidor.",
            ephemeral=True
        )
        return

    guild = interaction.guild
    painel_id = get_painel_canal_id(guild.id)
    aceite_id = get_aceite_canal_id(guild.id)
    logs_id = get_logs_canal_id(guild.id)
    logs_admin_id = get_log_admin_canal_id(guild.id)
    categoria_middle_id = get_middle_category_id(guild.id)

    embed = discord.Embed(
        title="Configuração de canais (/set)",
        color=cor_paleta("info")
    )
    embed.add_field(name="Painel (/setpainel)", value=_fmt_canal_configurado(guild, painel_id), inline=False)
    embed.add_field(name="Aceite (/setaceite)", value=_fmt_canal_configurado(guild, aceite_id), inline=False)
    embed.add_field(name="Logs (/setlogs)", value=_fmt_canal_configurado(guild, logs_id), inline=False)
    embed.add_field(name="Logs Admin (/setlogadmin)", value=_fmt_canal_configurado(guild, logs_admin_id), inline=False)
    embed.add_field(name="Categoria Middle (/setcmiddle)", value=_fmt_canal_configurado(guild, categoria_middle_id), inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="fn", description="Finaliza o ticket atual e gera os logs")
async def fn(interaction: discord.Interaction):
    if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message(
            "Este comando só pode ser usado dentro de um ticket no servidor.",
            ephemeral=True, delete_after=60
        )
        return

    canal = interaction.channel
    if canal.id not in ticket_type:
        await interaction.response.send_message(
            "Este comando só pode ser usado em um ticket ativo.",
            ephemeral=True, delete_after=60
        )
        return

    middle_id = ticket_middleman.get(canal.id)
    if middle_id is None:
        await interaction.response.send_message(
            "Este ticket ainda não foi assumido por um Middle.",
            ephemeral=True, delete_after=60
        )
        return
    if interaction.user.id != middle_id:
        await interaction.response.send_message(
            "Apenas o Middle que assumiu este ticket pode usar /fn.",
            ephemeral=True, delete_after=60
        )
        return

    lock = await ticket_lock_or_wait_msg(interaction, canal.id)
    if lock is None:
        return
    async with lock:
        await FecharTicketView(canal)._processar_fechamento(
            interaction,
            "✅ Finalizando ticket e registrando logs...",
            forcar_log=True,
            permitir_admin=False,
        )


@bot.tree.command(name="settaxa", description="Configura os valores da taxa do middle")
@app_commands.describe(
    faixa="Faixa da taxa que você quer alterar",
    valor="Novo valor (para percentual use decimal, ex: 0.02 = 2%)"
)
@app_commands.choices(
    faixa=[
        app_commands.Choice(name="Acima de R$700 (percentual)", value="acima_700_percentual"),
        app_commands.Choice(name="Acima de R$400 (fixo)", value="acima_400_fixo"),
        app_commands.Choice(name="Acima de R$100 (fixo)", value="acima_100_fixo"),
        app_commands.Choice(name="Acima de R$8 (fixo)", value="acima_8_fixo"),
        app_commands.Choice(name="Até R$8 (fixo)", value="ate_8_fixo"),
    ]
)
async def settaxa(
    interaction: discord.Interaction,
    faixa: app_commands.Choice[str],
    valor: float
):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "Apenas administradores podem usar este comando.",
            ephemeral=True, delete_after=60
        )
        return

    if valor < 0:
        await interaction.response.send_message(
            "O valor da taxa não pode ser negativo.",
            ephemeral=True, delete_after=60
        )
        return

    data = carregar_taxa_config(interaction.guild.id)
    data[faixa.value] = valor
    salvar_taxa_config_guild(interaction.guild.id, data)

    await interaction.response.send_message(
        f"✅ Taxa atualizada: **{faixa.name}** = `{valor}`",
        ephemeral=True, delete_after=60
    )


@bot.tree.command(name="cobrar", description="Gera um QR Code Pix no valor informado")
@app_commands.describe(valor="Valor para gerar a cobrança Pix")
async def cobrar(interaction: discord.Interaction, valor: float):
    role = get_middle_role(interaction.guild)
    is_middle = role in interaction.user.roles if role else False
    is_admin = interaction.user.guild_permissions.administrator

    if not (is_middle or is_admin):
        await interaction.response.send_message(
            "Apenas administradores ou quem tem o cargo de Middle configurado pode usar este comando.",
            ephemeral=True, delete_after=60
        )
        return

    if valor <= 0:
        await interaction.response.send_message(
            "Informe um valor maior que zero.",
            ephemeral=True, delete_after=60
        )
        return

    pix_info = get_pix_data(interaction.user.id)
    pix_key = pix_info.get("chave")
    pix_nome = pix_info.get("nome") or "Não informado"
    if not pix_key:
        await interaction.response.send_message(
            "Você ainda não cadastrou sua chave Pix. Use `/setpix` primeiro.",
            ephemeral=True, delete_after=60
        )
        return
    if not validar_chave_pix(pix_key):
        await interaction.response.send_message(
            "Sua chave PIX cadastrada é inválida. Atualize com `/setpix`.",
            ephemeral=True, delete_after=60
        )
        return

    qr = gerar_qrcode_pix(pix_key, valor)
    pix_copia_cola = gerar_payload_pix(pix_key, valor=f"{valor:.2f}")
    file = discord.File(fp=qr, filename="cobranca_pix.png")

    embed = discord.Embed(
        title="Cobrança Pix",
        description=(
            f"Titular: {pix_nome}\n"
            f"Código Pix: `{pix_copia_cola}`\n"
            f"Responsável: {interaction.user.mention}\n"
            f"Valor: R$ {valor:.2f}"
        ),
        color=cor_paleta("sucesso")
    )
    embed.set_image(url="attachment://cobranca_pix.png")

    await interaction.response.send_message(embed=embed, file=file)
    await interaction.followup.send(view=PixCopiaColaView(pix_copia_cola))


@bot.tree.command(name="mmt", description="Mostra o ranking diário de taxa dos Middle Man (hoje, BRT)")
async def mmt(interaction: discord.Interaction):
    role = get_middle_role(interaction.guild)
    is_middle = role in interaction.user.roles if role else False
    is_admin = interaction.user.guild_permissions.administrator

    if not (is_middle or is_admin):
        await interaction.response.send_message(
            "Apenas administradores ou quem tem o cargo de Middle configurado pode usar este comando.",
            ephemeral=True, delete_after=60
        )
        return

    ranking = ranking_mm_taxa_24h(interaction.guild.id)
    if not ranking:
        await interaction.response.send_message(
            "Nenhuma taxa registrada hoje (horário de Brasília).",
            ephemeral=True, delete_after=60
        )
        return

    linhas = []
    for i, (middle_id, total) in enumerate(ranking[:10], start=1):
        linhas.append(f"**{i}.** <@{middle_id}> — `R$ {total:.2f}`")

    embed = discord.Embed(
        title="📊 Ranking MM de hoje (BRT)",
        description="\n".join(linhas),
        color=cor_paleta("info")
    )
    embed.set_footer(text=f"Servidor: {interaction.guild.name}")
    await interaction.response.send_message(embed=embed)


token = os.getenv("DISCORD_TOKEN")
if not token:
    raise RuntimeError("Defina a variável de ambiente DISCORD_TOKEN antes de iniciar o bot.")

bot.run(token)
