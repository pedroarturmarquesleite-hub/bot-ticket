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
from logging.handlers import RotatingFileHandler
import time



ticket_count_by_guild = {}
ticket_middleman = {}
ticket_parties = {}
ticket_trade_parties = {}
ticket_loading_msg = {}
ticket_type = {}
ticket_negociacao = {}
APP_DATA_DIR = os.getenv("APP_DATA_DIR", os.getcwd())
os.makedirs(APP_DATA_DIR, exist_ok=True)
PANEL_CONFIG_FILE = os.path.join(APP_DATA_DIR, "panel_config.json")
ACEITE_CONFIG_FILE = os.path.join(APP_DATA_DIR, "aceite_config.json")
TAXA_CONFIG_FILE = os.path.join(APP_DATA_DIR, "taxa_config.json")
TICKET_STATE_FILE = os.path.join(APP_DATA_DIR, "ticket_state.json")
LOGS_CONFIG_FILE = os.path.join(APP_DATA_DIR, "logs_config.json")
ROLE_CONFIG_FILE = os.path.join(APP_DATA_DIR, "role_config.json")
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
    global ticket_count_by_guild, ticket_middleman, ticket_parties, ticket_trade_parties, ticket_type, ticket_loading_msg

    data = carregar_estado_tickets()
    ticket_count_by_guild = data["next_ticket_number_by_guild"]
    ticket_middleman = data["middleman"]
    ticket_parties = data["parties"]
    ticket_trade_parties = data["trade_parties"]
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


def remover_estado_ticket(canal_id):
    ticket_middleman.pop(canal_id, None)
    ticket_parties.pop(canal_id, None)
    ticket_trade_parties.pop(canal_id, None)
    ticket_loading_msg.pop(canal_id, None)
    ticket_type.pop(canal_id, None)
    ticket_negociacao.pop(canal_id, None)
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


async def enviar_log_fechamento_ticket(guild, canal, closed_by_id):
    if guild is None or canal is None:
        return

    canal_id = canal.id
    creator_id = None
    for alvo, overwrite in canal.overwrites.items():
        if isinstance(alvo, discord.Member) and overwrite.view_channel is True:
            if alvo.bot:
                continue
            if alvo.id == ticket_middleman.get(canal_id):
                continue
            creator_id = alvo.id
            break

    middle_id = ticket_middleman.get(canal_id)
    partes = ticket_parties.get(canal_id, {})
    partes_trade = ticket_trade_parties.get(canal_id, {})
    dados_negociacao = ticket_negociacao.get(canal_id, {}) if isinstance(ticket_negociacao.get(canal_id), dict) else {}
    tipo = ticket_type.get(canal_id, "desconhecido")

    valor_brainrot_txt = "Não informado"
    valor_taxa_txt = "Não informado"
    nome_brainrot_txt = "Não informado"

    if isinstance(dados_negociacao, dict):
        nome_brainrot = dados_negociacao.get("brainrot_nome")
        if isinstance(nome_brainrot, str) and nome_brainrot.strip():
            nome_brainrot_txt = nome_brainrot.strip()

        valor_negociado = dados_negociacao.get("valor")
        try:
            valor_negociado = float(valor_negociado)
        except (TypeError, ValueError):
            valor_negociado = None

        if valor_negociado is not None:
            valor_brainrot_txt = f"R$ {valor_negociado:.2f}"
            if tipo == "brainrot":
                valor_taxa_txt = "R$ 0.00 (taxa em item)"
            else:
                valor_taxa_txt = f"R$ {calcular_taxa(valor_negociado, guild.id):.2f}"

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

    # No log de "Participantes", mostramos quem foi adicionado pelo criador.
    # Por isso removemos criador e middle da lista.
    if creator_id is not None:
        ids_participantes.discard(creator_id)
    if middle_id is not None:
        ids_participantes.discard(middle_id)

    participantes_txt = "Nenhum participante adicional registrado."
    if ids_participantes:
        participantes_txt = "\n".join(
            f"- Adicionado: {_formatar_mencao_usuario(uid)}"
            for uid in sorted(ids_participantes)
        )

    mensagem = (
        f"Ticket fechado: {canal.name} (`{canal.id}`)\n"
        f"Tipo: {tipo}\n"
        f"Criador: {_formatar_mencao_usuario(creator_id)}\n"
        f"Middle: {_formatar_mencao_usuario(middle_id)}\n"
        f"Fechado por: {_formatar_mencao_usuario(closed_by_id)}\n"
        f"Valor do Brainrot negociado: {valor_brainrot_txt}\n"
        f"Valor da taxa: {valor_taxa_txt}\n"
        f"Brainrot informado: {nome_brainrot_txt}\n"
        f"Participantes:\n{participantes_txt}"
    )
    logger.info(mensagem)

    logs_channel_id = _id_int(get_logs_canal_id(guild.id))
    if logs_channel_id is None:
        return

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
            return

    if not isinstance(canal_logs, discord.TextChannel):
        logger.warning(
            "Canal configurado de logs nao eh TextChannel guild_id=%s channel_id=%s",
            guild.id,
            logs_channel_id
        )
        return

    tipo_base = "Troca venda/compra"
    cor = discord.Color.blurple()
    if tipo == "brainrot":
        tipo_base = "Troca de Brainrot"
        cor = discord.Color.orange()
    elif tipo == "trade":
        tipo_base = "Trade"
        cor = discord.Color.gold()

    participantes_resumo = []
    if creator_id is not None:
        participantes_resumo.append(f"<@{creator_id}>")
    if middle_id is not None and middle_id != creator_id:
        participantes_resumo.append(f"<@{middle_id}>")
    for uid in sorted(ids_participantes):
        if uid not in {creator_id, middle_id}:
            participantes_resumo.append(f"<@{uid}>")

    participantes_resumo_txt = " ".join(participantes_resumo) if participantes_resumo else "Não informado"

    valor_resumo = valor_brainrot_txt

    horario_txt = discord.utils.utcnow().strftime("%d/%m/%Y %H:%M UTC")

    embed = discord.Embed(
        title=f"🎟️ — {tipo_base} {canal.name} (Automático)",
        description="Uma movimentação de ticket foi finalizada, informações abaixo:",
        color=cor
    )
    embed.add_field(name="💲 Valor", value=valor_resumo, inline=False)
    embed.add_field(name="💸 Taxa", value=valor_taxa_txt, inline=False)
    embed.add_field(name="👥 Participantes", value=participantes_resumo_txt, inline=False)
    embed.add_field(name="🗓️ Horário", value=horario_txt, inline=False)
    embed.add_field(
        name="📌 Detalhes",
        value=(
            f"Tipo: `{tipo}`\n"
            f"Canal: {canal.mention}\n"
            f"Criador: {_formatar_mencao_usuario(creator_id)}\n"
            f"Middle: {_formatar_mencao_usuario(middle_id)}\n"
            f"Fechado por: {_formatar_mencao_usuario(closed_by_id)}\n"
            f"Brainrot: {nome_brainrot_txt}"
        ),
        inline=False
    )
    embed.set_footer(text=f"Ticket ID: {canal.id}")
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


migrar_dados_legados()
carregar_estado_tickets_memoria()


def carregar_painel_config():
    if not os.path.exists(PANEL_CONFIG_FILE):
        return {}
    with open(PANEL_CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def salvar_painel_config(data):
    with open(PANEL_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def set_painel_canal(guild_id, channel_id):
    data = carregar_painel_config()
    entry = data.get(str(guild_id))
    if isinstance(entry, dict):
        entry["channel_id"] = int(channel_id)
        data[str(guild_id)] = entry
    else:
        data[str(guild_id)] = {"channel_id": int(channel_id), "image_url": None}
    salvar_painel_config(data)


def get_painel_canal_id(guild_id):
    data = carregar_painel_config()
    entry = data.get(str(guild_id))
    if isinstance(entry, dict):
        return _id_int(entry.get("channel_id"))
    return _id_int(entry)


def set_painel_image_url(guild_id, image_url):
    data = carregar_painel_config()
    entry = data.get(str(guild_id))
    if isinstance(entry, dict):
        entry["image_url"] = image_url
        data[str(guild_id)] = entry
    else:
        data[str(guild_id)] = {"channel_id": _id_int(entry), "image_url": image_url}
    salvar_painel_config(data)


def get_painel_image_url(guild_id):
    data = carregar_painel_config()
    entry = data.get(str(guild_id))
    if isinstance(entry, dict):
        url = entry.get("image_url")
        if isinstance(url, str) and url.strip():
            return url.strip()
        return None
    return None


def validar_url_imagem(url):
    if not isinstance(url, str):
        return False
    url = url.strip()
    if not url:
        return False
    return url.startswith("http://") or url.startswith("https://")


def carregar_aceite_config():
    if not os.path.exists(ACEITE_CONFIG_FILE):
        return {}
    with open(ACEITE_CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def salvar_aceite_config(data):
    with open(ACEITE_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def set_aceite_canal(guild_id, channel_id):
    data = carregar_aceite_config()
    data[str(guild_id)] = channel_id
    salvar_aceite_config(data)


def get_aceite_canal_id(guild_id):
    data = carregar_aceite_config()
    return data.get(str(guild_id))


def carregar_logs_config():
    if not os.path.exists(LOGS_CONFIG_FILE):
        return {}
    with open(LOGS_CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def salvar_logs_config(data):
    with open(LOGS_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def set_logs_canal(guild_id, channel_id):
    data = carregar_logs_config()
    data[str(guild_id)] = channel_id
    salvar_logs_config(data)


def get_logs_canal_id(guild_id):
    data = carregar_logs_config()
    return data.get(str(guild_id))


def carregar_role_config():
    if not os.path.exists(ROLE_CONFIG_FILE):
        return {}
    with open(ROLE_CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def salvar_role_config(data):
    with open(ROLE_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def set_middle_role_id(guild_id, role_id):
    data = carregar_role_config()
    data[str(guild_id)] = int(role_id)
    salvar_role_config(data)


def get_middle_role_id(guild_id):
    data = carregar_role_config()
    return _id_int(data.get(str(guild_id)))


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


def _normalizar_taxa_config(cfg):
    base = TAXA_PADRAO.copy()
    if isinstance(cfg, dict):
        base.update(cfg)
    return base


def carregar_taxa_config(guild_id=None):
    if not os.path.exists(TAXA_CONFIG_FILE):
        return TAXA_PADRAO.copy()

    with open(TAXA_CONFIG_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Compatibilidade com formato antigo (global): { "acima_700_percentual": ... }
    if isinstance(data, dict) and any(k in data for k in TAXA_PADRAO.keys()):
        return _normalizar_taxa_config(data)

    if guild_id is None:
        return TAXA_PADRAO.copy()

    if isinstance(data, dict):
        guild_cfg = data.get(str(guild_id), {})
        return _normalizar_taxa_config(guild_cfg)

    return TAXA_PADRAO.copy()


def salvar_taxa_config_guild(guild_id, cfg):
    data = {}
    if os.path.exists(TAXA_CONFIG_FILE):
        with open(TAXA_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

    # Se ainda estiver no formato antigo, migra para objeto por servidor.
    if isinstance(data, dict) and any(k in data for k in TAXA_PADRAO.keys()):
        legado = _normalizar_taxa_config(data)
        data = {"_legacy_global": legado}
    elif not isinstance(data, dict):
        data = {}

    data[str(guild_id)] = _normalizar_taxa_config(cfg)

    with open(TAXA_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


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
        color=discord.Color.blue()
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
            await canal.send("⚠️ Canal de aceite não configurado. Um administrador deve usar `/setaceite`.")
            return

        try:
            aceite_channel = canal.guild.get_channel(int(aceite_canal_id))
        except (TypeError, ValueError):
            aceite_channel = None

        if not isinstance(aceite_channel, discord.TextChannel):
            await canal.send("⚠️ Canal de aceite não configurado. Um administrador deve usar `/setaceite`.")
            return

        ticket_kind = ticket_type.get(canal.id, "pix")
        tipo_middle = "Taxa Brain Rot" if ticket_kind == "brainrot" else "Taxa Pix"
        await aceite_channel.send(
            f"Ticket aguardando MM ({tipo_middle}): {canal.mention}",
            view=MiddlemanAcceptView(canal, comprador, vendedor)
        )

    async def _avisar_aceite_trade(self, canal, pessoa1, pessoa2):
        aceite_canal_id = get_aceite_canal_id(canal.guild.id)
        if not aceite_canal_id:
            await canal.send("⚠️ Canal de aceite não configurado. Um administrador deve usar `/setaceite`.")
            return

        try:
            aceite_channel = canal.guild.get_channel(int(aceite_canal_id))
        except (TypeError, ValueError):
            aceite_channel = None

        if not isinstance(aceite_channel, discord.TextChannel):
            await canal.send("⚠️ Canal de aceite não configurado. Um administrador deve usar `/setaceite`.")
            return

        await aceite_channel.send(
            f"Ticket aguardando MM (Trade): {canal.mention}",
            view=MiddlemanAcceptTradeView(canal, pessoa1, pessoa2)
        )

    async def _recuperar_ticket(self, canal: discord.TextChannel):
        kind = ticket_type.get(canal.id, "pix")
        middle_id = ticket_middleman.get(canal.id)

        await canal.send(
            "🔄 Bot reiniciado. Fluxo deste ticket foi recarregado.",
            view=FecharTicketView(canal)
        )

        if kind in {"pix", "brainrot"}:
            partes = obter_partes_ticket(canal)
            if not partes:
                criador = self._detectar_criador_ticket(canal)
                if criador:
                    view_setup = TradeSetupView(canal, criador)
                    msg = await canal.send(
                        "🔄 Bot reiniciado. Se necessário, refaça a definição de comprador e vendedor:",
                        view=view_setup
                    )
                    view_setup.message = msg
                return

            comprador = partes["comprador"]
            vendedor = partes["vendedor"]

            if middle_id is None:
                embed = discord.Embed(
                    title="⏳ Aguardando Middle Man",
                    description="🔄 Bot reiniciado. Um middle irá aceitar o ticket em breve...",
                    color=discord.Color.orange()
                )
                msg_loading = await canal.send(embed=embed)
                ticket_loading_msg[canal.id] = msg_loading
                await self._avisar_aceite_pix_brainrot(canal, comprador, vendedor)
                return

            middle = canal.guild.get_member(int(middle_id))
            if middle is None:
                ticket_middleman.pop(canal.id, None)
                salvar_estado_tickets()
                embed = discord.Embed(
                    title="⏳ Aguardando Middle Man",
                    description="🔄 O middle anterior não está disponível. Aguardando novo aceite...",
                    color=discord.Color.orange()
                )
                msg_loading = await canal.send(embed=embed)
                ticket_loading_msg[canal.id] = msg_loading
                await self._avisar_aceite_pix_brainrot(canal, comprador, vendedor)
                return

            await canal.set_permissions(middle, view_channel=True)
            iniciar_negociacao_ticket(canal.id, comprador, vendedor)
            view_valor = ValorView(canal, vendedor, comprador)
            msg = await canal.send(
                f"🔄 Bot reiniciado. {vendedor.mention}, informe o valor para continuar:",
                view=view_valor
            )
            view_valor.msg = msg
            view_brainrot = BrainrotNomeView(canal, comprador, vendedor)
            msg_brainrot = await canal.send(
                f"🔄 Bot reiniciado. {comprador.mention}, informe qual brainrot será vendido:",
                view=view_brainrot
            )
            view_brainrot.msg = msg_brainrot
            return

        if kind == "trade":
            partes_trade = obter_partes_trade(canal)
            if not partes_trade:
                criador = self._detectar_criador_ticket(canal)
                if criador:
                    view_trade = TradeSetupTradeView(canal, criador)
                    msg = await canal.send(
                        "?? Bot reiniciado. Refa?a a sele??o da pessoa da troca:",
                        view=view_trade
                    )
                    view_trade.message = msg
                return

            pessoa1 = partes_trade["pessoa1"]
            pessoa2 = partes_trade["pessoa2"]

            if middle_id is None:
                embed = discord.Embed(
                    title="⏳ Aguardando Middle Man",
                    description="🔄 Bot reiniciado. Um middle irá aceitar o ticket em breve...",
                    color=discord.Color.orange()
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
                    color=discord.Color.orange()
                )
                msg_loading = await canal.send(embed=embed)
                ticket_loading_msg[canal.id] = msg_loading
                await self._avisar_aceite_trade(canal, pessoa1, pessoa2)
                return

            await canal.set_permissions(middle, view_channel=True)
            await canal.send(
                "🔄 Bot reiniciado. Continue escolhendo a taxa da trade:",
                view=TradeTaxaEscolhaView(canal, pessoa1, pessoa2, middle.id)
            )
            return

    async def on_ready(self):
        logger.info("Bot %s ON", self.user)
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
        return False, "❌ Nenhum Middle vinculado a este ticket."

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
            color=discord.Color.green()
        )
        embed_qr.set_image(url="attachment://pix_total.png")
        await canal.send(embed=embed_qr, file=file)
        await canal.send(
            "📋 Código Pix copia e cola:",
            view=PixCopiaColaView(pix_copia_cola)
        )
        await canal.send(
            "⏳ Aguarde o Middle Man confirmar que recebeu o valor do *Brainrot* e o valor da *Taxa*...",
            view=ConfirmarPagamentoView(canal, comprador, vendedor)
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
            color=discord.Color.blue()
        )
        embed_item.set_image(url="attachment://pix_item.png")
        await canal.send(embed=embed_item, file=file_item)
        await canal.send(
            "📋 Código Pix copia e cola (item):",
            view=PixCopiaColaView(pix_copia_cola_item)
        )

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
            color=discord.Color.orange()
        )
        embed_taxa.set_image(url="attachment://pix_taxa.png")
        await canal.send(embed=embed_taxa, file=file_taxa)
        await canal.send(
            "📋 Código Pix copia e cola (taxa):",
            view=PixCopiaColaView(pix_copia_cola_taxa)
        )

        await canal.send(
            "⏳ Aguarde o Middle Man confirmar que recebeu o valor do *Brainrot* e o valor da *Taxa*...",
            view=ConfirmarPagamentoView(canal, comprador, vendedor)
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
            color=discord.Color.green()
        )
        embed_qr.set_image(url="attachment://pix_item_brainrot.png")
        await canal.send(embed=embed_qr, file=file)
        await canal.send(
            "📋 Código Pix copia e cola:",
            view=PixCopiaColaView(pix_copia_cola)
        )
        await canal.send(
            "⏳ Aguardando pagamento do comprador...",
            view=ConfirmarPagamentoBrainrotPixView(canal, comprador, vendedor)
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
            color=discord.Color.green()
        )
        embed.set_image(url="attachment://trade_pix.png")
        await canal.send(embed=embed, file=file)
        await canal.send(
            "📋 Código Pix copia e cola:",
            view=PixCopiaColaView(pix_copia_cola)
        )
        await canal.send(
            "Aguardando confirmação de pagamento...",
            view=ConfirmarPagamentoTradePixView(canal, pessoa1, pessoa2, middle_id)
        )
        return True, None

    return False, "❌ Fluxo de reenvio de QR não reconhecido."


class ReenviarQrPixView(discord.ui.View):
    def __init__(self, canal, modo, dados):
        super().__init__(timeout=1800)
        self.canal = canal
        self.modo = modo
        self.dados = dados

    @discord.ui.button(label="Tentar novamente enviar QR", style=discord.ButtonStyle.blurple)
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

        await interaction.followup.send("✅ QR reenviado com sucesso.", ephemeral=True)


class PixCopiaColaView(discord.ui.View):
    def __init__(self, payload):
        super().__init__(timeout=None)
        self.payload = payload

    @discord.ui.button(label="📋 Copiar código Pix", style=discord.ButtonStyle.secondary)
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
        await interaction.channel.send(mensagem)
        await interaction.response.send_message("Taxa definida.", ephemeral=True, delete_after=60)
        self.stop()

    @discord.ui.button(label="Comprador paga", style=discord.ButtonStyle.green)
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
            await interaction.channel.send(
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
                )
            )
            return
        if estado:
            estado["etapa"] = "aguardando_pagamento_middle"


    @discord.ui.button(label="Vendedor paga", style=discord.ButtonStyle.red)
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
            await interaction.channel.send(
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
                )
            )
            return
        if estado:
            estado["etapa"] = "aguardando_pagamento_middle"

class ConfirmarPagamentoView(discord.ui.View):
    def __init__(self, canal, comprador, vendedor):
        super().__init__(timeout=None)
        self.canal = canal
        self.comprador = comprador
        self.vendedor = vendedor

    @discord.ui.button(label="✅ Recebi o pagamento", style=discord.ButtonStyle.green)
    async def confirmar_pagamento(self, interaction, button):
        if await em_cooldown(interaction, "confirmar_pagamento_pix", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return

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
        await interaction.message.delete()

        if estado:
            estado["etapa"] = "aguardando_confirmacao_entrega"
        await self.canal.send(
            f"📦 {self.comprador.mention}, confirme que recebeu o Brainrot:",
            view=ConfirmarEntregaView(self.canal, self.comprador, self.vendedor)
        )

class ConfirmarTaxaBrainrotView(discord.ui.View):
    def __init__(self, canal, valor, comprador, vendedor):
        super().__init__(timeout=None)
        self.canal = canal
        self.valor = valor
        self.comprador = comprador
        self.vendedor = vendedor

    @discord.ui.button(label="Recebi a taxa em Brainrot", style=discord.ButtonStyle.green)
    async def confirmar_taxa_brainrot(self, interaction, button):
        if await em_cooldown(interaction, "confirmar_taxa_brainrot", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return

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
        await interaction.message.delete()

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
            await self.canal.send(
                f"{erro}\nMiddle: use `/setpix` e clique no botão abaixo para tentar novamente.",
                view=ReenviarQrPixView(
                    self.canal,
                    "brainrot_item",
                    {
                        "valor": self.valor,
                        "comprador": self.comprador,
                        "vendedor": self.vendedor
                    }
                )
            )
            return

class ConfirmarPagamentoBrainrotPixView(discord.ui.View):
    def __init__(self, canal, comprador, vendedor):
        super().__init__(timeout=None)
        self.canal = canal
        self.comprador = comprador
        self.vendedor = vendedor

    @discord.ui.button(label="Recebi o pagamento em PIX", style=discord.ButtonStyle.green)
    async def confirmar_pagamento(self, interaction, button):
        if await em_cooldown(interaction, "confirmar_pagamento_brainrot_pix", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return

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
        await interaction.message.delete()

        if estado:
            estado["etapa"] = "aguardando_confirmacao_entrega"
        await self.canal.send(
            f"📦 {self.comprador.mention}, confirme que recebeu o Brainrot:",
            view=ConfirmarEntregaView(self.canal, self.comprador, self.vendedor)
        )

class ConfirmarEntregaView(discord.ui.View):
    def __init__(self, canal, comprador, vendedor):
        super().__init__(timeout=None)
        self.canal = canal
        self.comprador = comprador
        self.vendedor = vendedor

    @discord.ui.button(label="📦 Recebi o Brainrot", style=discord.ButtonStyle.green)
    async def confirmar_item(self, interaction, button):
        if await em_cooldown(interaction, "confirmar_recebimento_item", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return

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
        await self.canal.send(
            f"{self.vendedor.mention}, envie sua chave Pix para que o Middle Man possa enviar o pix do Brainrot",
            view=EnviarPixView(self.canal, self.vendedor)
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

        await self.canal.send(
            f"💳 Pix do vendedor: \n*Apenas confirme o pagamento quando o Middle Man enviar o seu pix* \n`{chave}`",
            view=ConfirmarRecebimentoView(self.canal, self.vendedor)
        )
        if estado:
            estado["etapa"] = "aguardando_confirmacao_recebimento_vendedor"

        await interaction.response.send_message("Pix enviado.", ephemeral=True, delete_after=60)

class EnviarPixView(discord.ui.View):
    def __init__(self, canal, vendedor):
        super().__init__(timeout=None)
        self.canal = canal
        self.vendedor = vendedor

    @discord.ui.button(label="💳 Enviar meu Pix", style=discord.ButtonStyle.blurple)
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

    @discord.ui.button(label="💰 Recebi o pagamento", style=discord.ButtonStyle.green)
    async def confirmar_recebimento(self, interaction, button):
        if await em_cooldown(interaction, "confirmar_recebimento_vendedor", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return

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
            color=discord.Color.green()
        )

        await self.canal.send(embed=embed_finalizado)
        if estado:
            estado["etapa"] = "finalizado"

class FecharTicketView(discord.ui.View):
    def __init__(self, canal):
        super().__init__(timeout=None)
        self.canal = canal

    @discord.ui.button(label="🔒 Fechar Ticket", style=discord.ButtonStyle.red)
    async def fechar(self, interaction, button):
        if await em_cooldown(interaction, "fechar_ticket", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return

        middle_id = ticket_middleman.get(self.canal.id)
        is_admin = interaction.user.guild_permissions.administrator

        if interaction.user.id != middle_id and not is_admin:
            await interaction.response.send_message(
                "Apenas o Middle que assumiu o ticket ou um administrador pode fechar.",
                ephemeral=True, delete_after=60
            )
            return

        await interaction.response.send_message(
            "🔒 Fechando ticket...",
            ephemeral=True, delete_after=60
        )

        canal_id = self.canal.id
        guild = self.canal.guild
        closed_by_id = interaction.user.id

        try:
            await enviar_log_fechamento_ticket(guild, self.canal, closed_by_id)
        except Exception:
            logger.exception(
                "Falha ao registrar fechamento de ticket canal_id=%s guild_id=%s",
                canal_id,
                guild.id if guild else "desconhecida"
            )

        # limpa dados do ticket
        remover_estado_ticket(canal_id)
        
        # deleta canal
        await self.canal.delete()

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
        await canal.send("Não foi possível recuperar comprador/vendedor deste ticket.")
        return

    comprador = parties["comprador"]
    vendedor = parties["vendedor"]
    valor = float(estado["valor"])
    brainrot_nome = estado["brainrot_nome"]
    ticket_kind = ticket_type.get(canal.id, "pix")
    taxa = 0 if ticket_kind == "brainrot" else calcular_taxa(valor, canal.guild.id)

    descricao = (
        f"**Brainrot informado por {comprador.mention}:** `{brainrot_nome}`\n"
        f"**Valor informado por {vendedor.mention}:** R$ {valor:.2f}\n"
    )
    if ticket_kind != "brainrot":
        descricao += (
            f"**Taxa estimada:** R$ {taxa:.2f}\n"
            f"**Total estimado:** R$ {valor + taxa:.2f}\n"
        )
    descricao += (
        f"\n{comprador.mention}, confirme o valor.\n"
        f"{vendedor.mention}, confirme o brainrot."
    )

    embed = discord.Embed(
        title="Confirmação da negociação",
        description=descricao,
        color=discord.Color.gold()
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
        self.confirmar_valor.label = f"{self._nome_curto(self.comprador)} confirma valor"
        self.confirmar_brainrot.label = f"{self._nome_curto(self.vendedor)} confirma brainrot"

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
        try:
            await interaction.message.edit(view=None)
        except Exception:
            pass

        ticket_kind = ticket_type.get(self.canal.id, "pix")
        if ticket_kind == "brainrot":
            if estado:
                estado["etapa"] = "aguardando_taxa_brainrot_middle"
            await self.canal.send(
                "Enviem o servidor/brainrot da taxa para o Middle.\n"
                "Quando receber, o Middle confirma abaixo:",
                view=ConfirmarTaxaBrainrotView(
                    self.canal,
                    self.valor,
                    self.comprador,
                    self.vendedor
                )
            )
        else:
            if estado:
                estado["etapa"] = "aguardando_escolha_taxa"
            await self.canal.send(
                f"💸 {self.comprador.mention}, informe quem irá pagar a taxa para o Middle Man.",
                view=TaxaView(self.valor, self.comprador, self.vendedor, self.canal.guild.id)
            )
        self.stop()

    @discord.ui.button(label="Comprador confirma valor", style=discord.ButtonStyle.green)
    async def confirmar_valor(self, interaction, button):
        if await em_cooldown(interaction, "confirmar_valor", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return
        if interaction.user != self.comprador:
            await interaction.response.send_message(
                "Somente o comprador confirma o valor.",
                ephemeral=True, delete_after=60
            )
            return
        self.valor_confirmado = True
        await interaction.response.send_message("Valor confirmado.", ephemeral=True, delete_after=60)
        await self._seguir_fluxo(interaction)

    @discord.ui.button(label="Vendedor confirma brainrot", style=discord.ButtonStyle.blurple)
    async def confirmar_brainrot(self, interaction, button):
        if await em_cooldown(interaction, "confirmar_brainrot", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return
        if interaction.user != self.vendedor:
            await interaction.response.send_message(
                "Somente o vendedor confirma o brainrot.",
                ephemeral=True, delete_after=60
            )
            return
        self.brainrot_confirmado = True
        await interaction.response.send_message("Brainrot confirmado.", ephemeral=True, delete_after=60)
        await self._seguir_fluxo(interaction)


class ValorModal(discord.ui.Modal, title="Valor da negociação"):
    valor = discord.ui.TextInput(label="Digite o valor")

    def __init__(self, canal, mensagem, comprador, vendedor):
        super().__init__()
        self.canal = canal
        self.mensagem = mensagem
        self.comprador = comprador
        self.vendedor = vendedor

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
        if estado:
            estado["etapa"] = "aguardando_pagamento_brainrot_pix"
        if estado.get("confirm_msg_id"):
            await interaction.response.send_message(
                "A confirmação da negociação já foi enviada. Use os botões de confirmação.",
                ephemeral=True,
                delete_after=60
            )
            return
        estado["valor"] = valor

        try:
            await self.mensagem.delete()
        except Exception:
            pass

        await self.canal.send(
            f"✅ Valor registrado: R$ {valor:.2f}\n"
            f"Aguardando o comprador informar o brainrot."
        )
        await tentar_publicar_confirmacao_negociacao(self.canal)
        await interaction.response.send_message("Valor salvo.", ephemeral=True, delete_after=60)


class ValorView(discord.ui.View):
    def __init__(self, canal, vendedor, comprador):
        super().__init__(timeout=None)
        self.canal = canal
        self.vendedor = vendedor
        self.comprador = comprador
        self.msg = None

    @discord.ui.button(label="Informar valor", style=discord.ButtonStyle.blurple)
    async def informar(self, interaction, button):
        if interaction.user != self.vendedor:
            await interaction.response.send_message(
                "Somente vendedor pode informar.",
                ephemeral=True, delete_after=60
            )
            return
        await interaction.response.send_modal(
            ValorModal(self.canal, self.msg, self.comprador, self.vendedor)
        )


class BrainrotNomeModal(discord.ui.Modal, title="Brainrot da negociação"):
    brainrot_nome = discord.ui.TextInput(label="Qual brainrot será vendido?", max_length=120)

    def __init__(self, canal, mensagem, comprador, vendedor):
        super().__init__()
        self.canal = canal
        self.mensagem = mensagem
        self.comprador = comprador
        self.vendedor = vendedor

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

        try:
            await self.mensagem.delete()
        except Exception:
            pass

        await self.canal.send(
            f"✅ Brainrot registrado: `{nome}`\n"
            f"Aguardando o vendedor informar o valor."
        )
        await tentar_publicar_confirmacao_negociacao(self.canal)
        await interaction.response.send_message("Brainrot salvo.", ephemeral=True, delete_after=60)


class BrainrotNomeView(discord.ui.View):
    def __init__(self, canal, comprador, vendedor):
        super().__init__(timeout=None)
        self.canal = canal
        self.comprador = comprador
        self.vendedor = vendedor
        self.msg = None

    @discord.ui.button(label="Informar brainrot", style=discord.ButtonStyle.blurple)
    async def informar_brainrot(self, interaction, button):
        if interaction.user != self.comprador:
            await interaction.response.send_message(
                "Somente o comprador pode informar o brainrot.",
                ephemeral=True, delete_after=60
            )
            return
        await interaction.response.send_modal(
            BrainrotNomeModal(self.canal, self.msg, self.comprador, self.vendedor)
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

    @discord.ui.button(label="Aceitar Ticket", style=discord.ButtonStyle.green)
    async def aceitar(self, interaction, button):
        if await em_cooldown(interaction, "aceitar_ticket_middle", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
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
            color=discord.Color.green()
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

        iniciar_negociacao_ticket(self.canal.id, self.comprador, self.vendedor)
        view_valor = ValorView(self.canal, self.vendedor, self.comprador)
        msg = await self.canal.send(f"{self.vendedor.mention} **informe o valor que irá vender o BrainRot:**", view=view_valor)
        view_valor.msg = msg
        view_brainrot = BrainrotNomeView(self.canal, self.comprador, self.vendedor)
        msg_brainrot = await self.canal.send(
            f"{self.comprador.mention} **informe qual o nome brainrot você vai negociar:**",
            view=view_brainrot
        )
        view_brainrot.msg = msg_brainrot


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
                color=discord.Color.green()
            )
            await self.canal.send(embed=embed_finalizado)

    @discord.ui.button(label="Pessoa 1 confirmou", style=discord.ButtonStyle.blurple)
    async def confirmar_p1(self, interaction, button):
        if await em_cooldown(interaction, "trade_confirmar_p1", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return

        if interaction.user != self.pessoa1:
            await interaction.response.send_message("Apenas a pessoa 1 pode clicar aqui.", ephemeral=True, delete_after=60)
            return
        self.p1_ok = True
        await interaction.response.send_message("Confirmação recebida.", ephemeral=True, delete_after=60)
        await self._verificar_finalizacao(interaction)

    @discord.ui.button(label="Pessoa 2 confirmou", style=discord.ButtonStyle.green)
    async def confirmar_p2(self, interaction, button):
        if await em_cooldown(interaction, "trade_confirmar_p2", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return

        if interaction.user != self.pessoa2:
            await interaction.response.send_message("Apenas a pessoa 2 pode clicar aqui.", ephemeral=True, delete_after=60)
            return
        self.p2_ok = True
        await interaction.response.send_message("Confirmação recebida.", ephemeral=True, delete_after=60)
        await self._verificar_finalizacao(interaction)


class ConfirmarPagamentoTradePixView(discord.ui.View):
    def __init__(self, canal, pessoa1, pessoa2, middle_id):
        super().__init__(timeout=None)
        self.canal = canal
        self.pessoa1 = pessoa1
        self.pessoa2 = pessoa2
        self.middle_id = middle_id

    @discord.ui.button(label="Recebi o pagamento", style=discord.ButtonStyle.green)
    async def confirmar_pagamento(self, interaction, button):
        if await em_cooldown(interaction, "trade_confirmar_pagamento_pix", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return

        if interaction.user.id != self.middle_id:
            await interaction.response.send_message("Apenas o Middle pode confirmar o pagamento.", ephemeral=True, delete_after=60)
            return
        await interaction.response.defer()
        await interaction.message.delete()
        await self.canal.send(
            f"{self.pessoa1.mention} e {self.pessoa2.mention}, confirmem se a troca foi feita:",
            view=TradeFinalConfirmView(self.canal, self.pessoa1, self.pessoa2)
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
            await self.canal.send(
                f"{erro}\nMiddle: use `/setpix` e clique no botão abaixo para tentar novamente.",
                view=ReenviarQrPixView(
                    self.canal,
                    "trade_pix",
                    {
                        "valor": valor,
                        "pessoa1": self.pessoa1,
                        "pessoa2": self.pessoa2
                    }
                )
            )
            await interaction.response.send_message(
                "Não foi possível gerar o QR agora. Configure o PIX e use o botão no ticket.",
                ephemeral=True,
                delete_after=60
            )
            return

        await interaction.response.send_message("Cobrança enviada.", ephemeral=True, delete_after=60)


class TradePixValorView(discord.ui.View):
    def __init__(self, canal, pessoa1, pessoa2, middle_id):
        super().__init__(timeout=None)
        self.canal = canal
        self.pessoa1 = pessoa1
        self.pessoa2 = pessoa2
        self.middle_id = middle_id

    @discord.ui.button(label="Informar valor do PIX", style=discord.ButtonStyle.green)
    async def informar_valor(self, interaction, button):
        if await em_cooldown(interaction, "trade_informar_valor_pix", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return

        if interaction.user.id != self.middle_id:
            await interaction.response.send_message("Apenas o Middle pode informar o valor.", ephemeral=True, delete_after=60)
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

    @discord.ui.button(label="Taxa Pix", style=discord.ButtonStyle.green)
    async def taxa_pix(self, interaction, button):
        if await em_cooldown(interaction, "trade_escolher_taxa", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return

        if interaction.user.id != self.middle_id:
            await interaction.response.send_message("Apenas o Middle pode escolher a taxa.", ephemeral=True, delete_after=60)
            return
        await interaction.response.send_message(
            "Middle, informe o valor da taxa em reais",
            view=TradePixValorView(self.canal, self.pessoa1, self.pessoa2, self.middle_id)
        )

    @discord.ui.button(label="Taxa Brainrot", style=discord.ButtonStyle.blurple)
    async def taxa_brainrot(self, interaction, button):
        if await em_cooldown(interaction, "trade_escolher_taxa", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return

        if interaction.user.id != self.middle_id:
            await interaction.response.send_message("Apenas o Middle pode escolher a taxa.", ephemeral=True, delete_after=60)
            return
        await interaction.response.send_message(
            "O middle vai receber o Brainrot da taxa. Em seguida, a troca continuará.",
            view=ConfirmarTaxaTradeBrainrotView(self.canal, self.pessoa1, self.pessoa2, self.middle_id)
        )


class ConfirmarTaxaTradeBrainrotView(discord.ui.View):
    def __init__(self, canal, pessoa1, pessoa2, middle_id):
        super().__init__(timeout=None)
        self.canal = canal
        self.pessoa1 = pessoa1
        self.pessoa2 = pessoa2
        self.middle_id = middle_id

    @discord.ui.button(label="Recebi a taxa em Brainrot", style=discord.ButtonStyle.green)
    async def confirmar_taxa(self, interaction, button):
        if await em_cooldown(interaction, "trade_confirmar_taxa_brainrot", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
            return

        if interaction.user.id != self.middle_id:
            await interaction.response.send_message(
                "Apenas o Middle pode confirmar o recebimento da taxa.",
                ephemeral=True, delete_after=60
            )
            return

        await interaction.response.defer()
        await interaction.message.delete()

        await self.canal.send(
            f"{self.pessoa1.mention} e {self.pessoa2.mention}, confirmem se a troca foi feita:",
            view=TradeFinalConfirmView(self.canal, self.pessoa1, self.pessoa2)
        )


class MiddlemanAcceptTradeView(discord.ui.View):
    def __init__(self, canal, pessoa1, pessoa2):
        super().__init__(timeout=None)
        self.canal = canal
        self.pessoa1 = pessoa1
        self.pessoa2 = pessoa2

    @discord.ui.button(label="Aceitar Ticket", style=discord.ButtonStyle.green)
    async def aceitar(self, interaction, button):
        if await em_cooldown(interaction, "aceitar_ticket_trade", COOLDOWN_CLIQUE_CRITICO_SEGUNDOS):
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
            color=discord.Color.green()
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

        await self.canal.send(
            "Qual a taxa da trade? (Pix ou Brainrot)",
            view=TradeTaxaEscolhaView(self.canal, self.pessoa1, self.pessoa2, interaction.user.id)
        )


class TradeSetupTradeView(discord.ui.View):
    def __init__(self, canal, criador):
        super().__init__(timeout=None)
        self.canal = canal
        self.criador = criador
        self.message = None
        self.escolha_feita = False

    @discord.ui.button(label="Adicionar pessoa da troca", style=discord.ButtonStyle.blurple)
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

        await self.message.edit(
            content=f"Pessoa 1: {self.criador.mention}\nPessoa 2: {membro.mention}",
            view=None
        )
        await interaction.response.send_message("Pessoa adicionada.", ephemeral=True, delete_after=60)

        embed = discord.Embed(
            title="⏳ Aguardando Middle Man",
            description="🔄 Um middle irá aceitar o ticket em breve...",
            color=discord.Color.orange()
        )
        msg_loading = await self.canal.send(embed=embed)
        ticket_loading_msg[self.canal.id] = msg_loading

        aceite_channel = None
        aceite_canal_id = get_aceite_canal_id(self.canal.guild.id)
        if aceite_canal_id:
            try:
                aceite_channel = self.canal.guild.get_channel(int(aceite_canal_id))
            except (TypeError, ValueError):
                aceite_channel = None

        if isinstance(aceite_channel, discord.TextChannel):
            await aceite_channel.send(
                f"Ticket aguardando MM (Trade): {self.canal.mention}",
                view=MiddlemanAcceptTradeView(self.canal, self.criador, membro)
            )
        else:
            await self.canal.send("⚠️ Canal de aceite não configurado. Um administrador deve usar `/setaceite`.")


class TradeSetupView(discord.ui.View):
    def __init__(self, canal, criador):
        super().__init__(timeout=None)
        self.canal = canal
        self.criador = criador
        self.message = None
        self.comprador = None
        self.vendedor = None
        self.escolha_feita = False

    async def finalizar(self, interaction):

        # salva partes do ticket
        salvar_partes_ticket(self.canal.id, self.comprador, self.vendedor)

        await self.message.edit(
            content=f"Comprador: {self.comprador.mention}\nVendedor: {self.vendedor.mention}",
            view=None
        )

        embed = discord.Embed(
            title="⏳ Aguardando Middle Man",
            description="🔄 Um middle irá aceitar o ticket em breve...",
            color=discord.Color.orange()
        )

        msg_loading = await self.canal.send(embed=embed)

        # salva loading
        ticket_loading_msg[self.canal.id] = msg_loading

        aceite_channel = None
        aceite_canal_id = get_aceite_canal_id(self.canal.guild.id)
        if aceite_canal_id:
            try:
                aceite_channel = self.canal.guild.get_channel(int(aceite_canal_id))
            except (TypeError, ValueError):
                aceite_channel = None

        if isinstance(aceite_channel, discord.TextChannel):
            ticket_kind = ticket_type.get(self.canal.id, "pix")
            tipo_middle = "Taxa Brain Rot" if ticket_kind == "brainrot" else "Taxa Pix"
            await aceite_channel.send(
                f"Ticket aguardando MM ({tipo_middle}): {self.canal.mention}",
                view=MiddlemanAcceptView(
                    self.canal,
                    self.comprador,
                    self.vendedor
                )
            )
        else:
            await self.canal.send(
                "⚠️ Canal de aceite não configurado. Um administrador deve usar `/setaceite`."
            )

    # -------- botão comprador --------
    @discord.ui.button(label="Vou Pagar/Comprador", style=discord.ButtonStyle.blurple)
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
    @discord.ui.button(label="Vou Receber/Vendedor", style=discord.ButtonStyle.green)
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
        membro = interaction.guild.get_member(
            int(interaction.data["values"][0])
        )

        self.vendedor = membro

        await self.canal.set_permissions(
            membro,
            view_channel=True
        )

        await interaction.response.send_message(
            "Vendedor definido.",
            ephemeral=True, delete_after=60
        )

        await self.finalizar(interaction)

    # -------- seleção comprador --------
    async def select_comprador(self, interaction):
        membro = interaction.guild.get_member(
            int(interaction.data["values"][0])
        )

        self.comprador = membro

        await self.canal.set_permissions(
            membro,
            view_channel=True
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

    def proximo_numero_ticket_pix(self, guild):
        guild_id = guild.id

        pattern = re.compile(r"^📩-ticket-(\d+)$")
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

    async def obter_ou_criar_categoria_middle(self, guild):
        nome_categoria = "middle man"
        for categoria in guild.categories:
            if categoria.name.strip().lower() == nome_categoria:
                return categoria

        return await guild.create_category(nome_categoria)

    async def criar_ticket_middleman_pix(self, interaction):
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
        aviso = discord.Embed(
            title="📢 LEIA COM ATENÇÃO",
            description=(
                "A taxa de middleman não é reembolsável.\n\n"
                "Ao assumir um ticket, o middle reserva tempo, disponibilidade e responsabilidade exclusiva para aquela negociação, deixando de atender outros atendimentos. Dessa forma, o serviço é considerado iniciado no momento da designação do middle, independentemente da conclusão da trade.\n\n"
                "Em caso de desistência de qualquer das partes após o pagamento, não há reembolso da taxa, conforme regras do servidor e os princípios da prestação de serviços e da boa-fé objetiva previstos no Código Civil Brasileiro.\n\n"
                "Ao efetuar o pagamento, o usuário declara estar ciente e de acordo com essa política.\n\n"
                f"Obrigado.{interaction.user.mention}"
            ),
            color=discord.Color.blue()
        )
        await canal.send(
            f"👋 {interaction.user.mention} **seu ticket foi aberto com sucesso!**\n\n"
            "*Responda as perguntas para continuar o atendimento.*"
        )

        await canal.send(
            embed=aviso,
            view=FecharTicketView(canal)
        )

        view = TradeSetupView(canal, interaction.user)
        msg = await canal.send("> Você vai **PAGAR** ou **RECEBER** o dinheiro", view=view)
        view.message = msg

        embed = discord.Embed(
            description=(
                f"✅ | {interaction.user.mention}, seu ticket foi aberto!\n"
                "Clique abaixo para encontrá-lo."
            ),
            color=discord.Color.green()
        )

        link_view = discord.ui.View()
        link_view.add_item(
            discord.ui.Button(
                label="Ir para o ticket",
                url=canal.jump_url
            )
        )

        await interaction.response.send_message(
            embed=embed,
            view=link_view,
            ephemeral=True, delete_after=60
        )

    async def criar_ticket_middleman_brainrot(self, interaction):
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

        aviso = discord.Embed(
            title="📢 LEIA COM ATENÇÃO",
            description=(
                "A taxa de middleman não é reembolsável.\n\n"
                "Ao assumir um ticket, o middle reserva tempo, disponibilidade e responsabilidade exclusiva para aquela negociação, deixando de atender outros atendimentos. Dessa forma, o serviço é considerado iniciado no momento da designação do middle, independentemente da conclusão da trade.\n\n"
                "Em caso de desistência de qualquer das partes após o pagamento, não há reembolso da taxa, conforme regras do servidor e os princípios da prestação de serviços e da boa-fé objetiva previstos no Código Civil Brasileiro.\n\n"
                "Ao efetuar o pagamento, o usuário declara estar ciente e de acordo com essa política.\n\n"
                f"Obrigado.{interaction.user.mention}"
            ),
            color=discord.Color.blue()
        )

        await canal.send(
            f"👋 {interaction.user.mention} **seu ticket foi aberto com sucesso!**\n\n"
            "*Responda as perguntas para continuar o atendimento.*"
        )

        await canal.send(
            embed=aviso,
            view=FecharTicketView(canal)
        )

        view = TradeSetupView(canal, interaction.user)
        msg = await canal.send("> Você é comprador ou vendedor?", view=view)
        view.message = msg

        embed = discord.Embed(
            description=(
                f"✅ | {interaction.user.mention}, seu ticket foi aberto!\n"
                "Clique abaixo para encontrá-lo."
            ),
            color=discord.Color.green()
        )

        link_view = discord.ui.View()
        link_view.add_item(
            discord.ui.Button(
                label="Ir para o ticket",
                url=canal.jump_url
            )
        )

        await interaction.response.send_message(
            embed=embed,
            view=link_view,
            ephemeral=True, delete_after=60
        )

    async def criar_ticket_middleman_trade(self, interaction):
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

        aviso = discord.Embed(
            title="📢 LEIA COM ATENÇÃO",
            description=(
                "A taxa de middleman não é reembolsável.\n\n"
                "Ao assumir um ticket, o middle reserva tempo, disponibilidade e responsabilidade exclusiva para aquela negociação, deixando de atender outros atendimentos. Dessa forma, o serviço é considerado iniciado no momento da designação do middle, independentemente da conclusão da trade.\n\n"
                "Em caso de desistência de qualquer das partes após o pagamento, não há reembolso da taxa, conforme regras do servidor e os princípios da prestação de serviços e da boa-fé objetiva previstos no Código Civil Brasileiro.\n\n"
                "Ao efetuar o pagamento, o usuário declara estar ciente e de acordo com essa política.\n\n"
                f"Obrigado.{interaction.user.mention}"
            ),
            color=discord.Color.blue()
        )

        await canal.send(
            f"👋 {interaction.user.mention} **seu ticket foi aberto com sucesso!**\n\n"
            "*Responda as perguntas para continuar o atendimento.*"
        )

        await canal.send(
            embed=aviso,
            view=FecharTicketView(canal)
        )

        view_trade = TradeSetupTradeView(canal, interaction.user)
        msg = await canal.send("> Com quem você vai trocar?", view=view_trade)
        view_trade.message = msg

        embed = discord.Embed(
            description=(
                f"✅ | {interaction.user.mention}, seu ticket de trade foi aberto!\n"
                "Clique abaixo para encontrá-lo."
            ),
            color=discord.Color.green()
        )

        link_view = discord.ui.View()
        link_view.add_item(
            discord.ui.Button(
                label="Ir para o ticket",
                url=canal.jump_url
            )
        )

        await interaction.response.send_message(
            embed=embed,
            view=link_view,
            ephemeral=True, delete_after=60
        )

    class EscolhaTaxaMiddleView(discord.ui.View):
        def __init__(self, ticket_view):
            super().__init__(timeout=120)
            self.ticket_view = ticket_view

        @discord.ui.button(label="Venda/Compra | Taxa Pix", style=discord.ButtonStyle.green)
        async def taxa_pix(self, interaction, button):
            if await em_cooldown(interaction, "abrir_ticket_middle", COOLDOWN_ABRIR_TICKET_SEGUNDOS):
                return
            await self.ticket_view.criar_ticket_middleman_pix(interaction)

        @discord.ui.button(label="Venda/Compra | Taxa Brainrot", style=discord.ButtonStyle.blurple)
        async def taxa_brainrot(self, interaction, button):
            if await em_cooldown(interaction, "abrir_ticket_middle", COOLDOWN_ABRIR_TICKET_SEGUNDOS):
                return
            await self.ticket_view.criar_ticket_middleman_brainrot(interaction)

        @discord.ui.button(label="Trade", style=discord.ButtonStyle.red)
        async def trade(self, interaction, button):
            if await em_cooldown(interaction, "abrir_ticket_middle", COOLDOWN_ABRIR_TICKET_SEGUNDOS):
                return
            await self.ticket_view.criar_ticket_middleman_trade(interaction)

    @discord.ui.button(label="💠Solicitar Middle Man", style=discord.ButtonStyle.green)
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

    await interaction.response.send_message(
        f"Canal de logs configurado com sucesso em {canal.mention}.",
        ephemeral=True, delete_after=60
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
        color=discord.Color.green()
    )
    embed.set_image(url="attachment://cobranca_pix.png")

    await interaction.response.send_message(embed=embed, file=file)
    await interaction.followup.send(
        "📋 Código Pix copia e cola:",
        view=PixCopiaColaView(pix_copia_cola)
    )


token = os.getenv("DISCORD_TOKEN")
if not token:
    raise RuntimeError("Defina a variável de ambiente DISCORD_TOKEN antes de iniciar o bot.")

bot.run(token)
