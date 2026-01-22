import gspread
import os
from google.oauth2.service_account import Credentials
from datetime import datetime

SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

_client = None
_spreadsheet = None

def get_client():
    global _client
    if _client: return _client
    
    raw_pk = os.getenv("G_PRIVATE_KEY", "").strip().strip('"').strip("'")
    header = "-----BEGIN PRIVATE KEY-----"
    if header in raw_pk and not any(x in raw_pk for x in ["\\n", "\n"]):
        raw_pk = raw_pk.replace(header, header + "\\n")
    if "\\n" in raw_pk:
        raw_pk = raw_pk.replace("\\n", "\n")

    creds_dict = {
        "type": "service_account",
        "project_id": os.getenv("G_PROJECT_ID"),
        "private_key_id": os.getenv("G_PRIVATE_KEY_ID"),
        "private_key": raw_pk,
        "client_email": os.getenv("G_CLIENT_EMAIL"),
        "client_id": os.getenv("G_CLIENT_ID"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": os.getenv("G_CERT_URL")
    }

    creds = Credentials.from_service_account_info(creds_dict).with_scopes(SCOPES)
    _client = gspread.authorize(creds)
    return _client
    
def get_spreadsheet():
    global _spreadsheet
    if _spreadsheet:
        try:
            _spreadsheet.title
            return _spreadsheet
        except:
            _spreadsheet = None
            
    if not _spreadsheet:
        _spreadsheet = get_client().open(os.getenv("SPREADSHEET_NAME"))
    return _spreadsheet

# --- ФУНКЦІЇ, ЩО ЗАЛИШАЮТЬСЯ ДЛЯ ЗАПИСУ/ОНОВЛЕННЯ ---

def find_player_by_nick(nick: str):
    # Використовується тільки при реєстрації (раз на юзера)
    ws = get_spreadsheet().worksheet("Гравці🕵️‍♂️")
    rows = ws.get_all_values()
    search_nick = nick.strip().lower()
    for i, row in enumerate(rows[1:], 2):
        if len(row) > 1 and row[1].strip().lower() == search_nick:
            return i, row
    return None, None

def bind_telegram_id(row_index: int, telegram_id: int):
    # Записує ID в таблицю
    try:
        ws = get_spreadsheet().worksheet("Гравці🕵️‍♂️")
        ws.update_cell(row_index, 4, str(telegram_id))
    except Exception as e:
        print(f"Помилка запису: {e}")

def add_purchase(nick: str, item_name: str, price: int):
    # Додає рядок покупки
    get_spreadsheet().worksheet("Покупки🛒").append_row([
        datetime.now().strftime("%d.%m.%Y"), nick, item_name, price
    ])

def get_market_items():
    # Використовується кешем для початкового завантаження
    rows = get_spreadsheet().worksheet("🖤MafMarket🖤").get_all_values()
    return [{"name": r[0], "price": r[1], "description": r[2], "level": r[3]} for r in rows[2:] if r and r[0]]
