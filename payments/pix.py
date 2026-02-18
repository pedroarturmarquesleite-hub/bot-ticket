import qrcode
from io import BytesIO
import json
import os
import re

BASE_DIR = os.path.dirname(__file__)
APP_DATA_DIR = os.getenv("APP_DATA_DIR", os.getcwd())
PIX_FILE = os.path.join(APP_DATA_DIR, "pix_keys.json")
LEGACY_PIX_FILE = os.path.join(BASE_DIR, "pix_keys.json")
LEGACY_PIX_FILE_ROOT = os.path.join(os.getcwd(), "pix_keys.json")



def carregar_pix():
    if not os.path.exists(PIX_FILE):
        # Compatibilidade com local antigo (payments/pix_keys.json).
        for legado in (LEGACY_PIX_FILE, LEGACY_PIX_FILE_ROOT):
            if os.path.exists(legado):
                with open(legado, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Migra para o caminho novo na primeira leitura.
                salvar_pix(data)
                return data
        return {}

    with open(PIX_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def salvar_pix(data):
    os.makedirs(os.path.dirname(PIX_FILE), exist_ok=True)
    with open(PIX_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def set_pix(user_id, chave, nome=None):
    data = carregar_pix()
    data[str(user_id)] = {
        "chave": chave,
        "nome": nome or ""
    }
    salvar_pix(data)


def get_pix(user_id):
    info = get_pix_data(user_id)
    return info.get("chave")


def get_pix_data(user_id):
    data = carregar_pix()
    value = data.get(str(user_id))

    if value is None:
        return {"chave": None, "nome": ""}

    # Compatibilidade com formato antigo: apenas string da chave.
    if isinstance(value, str):
        return {"chave": value, "nome": ""}

    if isinstance(value, dict):
        return {
            "chave": value.get("chave"),
            "nome": value.get("nome", "")
        }

    return {"chave": None, "nome": ""}


def validar_chave_pix(chave):
    if not isinstance(chave, str):
        return False

    chave = chave.strip()
    if not chave:
        return False

    # Email
    if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", chave):
        return True

    # Telefone em padrão E.164 (ex.: +5511999999999)
    if re.fullmatch(r"\+[1-9]\d{9,14}", chave):
        return True

    # Chave aleatória UUID
    if re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}",
        chave
    ):
        return True

    # CPF/CNPJ (com ou sem pontuação)
    digits = re.sub(r"\D", "", chave)
    if len(digits) in (11, 14):
        return True

    return False


# ---------- CRC16 ----------
def crc16(payload: str) -> str:
    polinomio = 0x1021
    resultado = 0xFFFF

    for c in payload:
        resultado ^= ord(c) << 8
        for _ in range(8):
            if resultado & 0x8000:
                resultado = (resultado << 1) ^ polinomio
            else:
                resultado <<= 1
            resultado &= 0xFFFF

    return format(resultado, '04X')


# ---------- PAYLOAD PIX ----------
def gerar_payload_pix(chave, nome="MIDDLEMAN", cidade="BRASIL", valor="0.00"):

    gui = "BR.GOV.BCB.PIX"

    merchant_account = (
        "00" + f"{len(gui):02}" + gui +
        "01" + f"{len(chave):02}" + chave
    )

    merchant_account_len = f"{len(merchant_account):02}"

    payload = (
        "000201"                                  
        "26" + merchant_account_len + merchant_account +
        "52040000"                                
        "5303986"                                 
        "54" + f"{len(valor):02}" + valor +       
        "5802BR"                                  
        "59" + f"{len(nome):02}" + nome +         
        "60" + f"{len(cidade):02}" + cidade +     
        "62070503***"                             
        "6304"
    )

    payload += crc16(payload)

    return payload

# ---------- QR CODE ----------
def gerar_qrcode_pix(chave, valor):
    if valor <= 0:
        raise ValueError("Valor Pix inválido")

    valor_formatado = f"{valor:.2f}"

    payload = gerar_payload_pix(chave, valor=valor_formatado)

    qr = qrcode.make(payload)

    buffer = BytesIO()
    qr.save(buffer, format="PNG")
    buffer.seek(0)

    return buffer
