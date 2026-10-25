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
    
def get_players_sheet():
    return get_spreadsheet().worksheet("Гравці🕵️‍♂️")
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
    """Записує Telegram ID у колонку D; помилки передає виклику."""
    ws = get_players_sheet()
    ws.update_cell(row_index, 4, str(telegram_id))


def get_player_by_telegram_id(telegram_id: int):
    """Повертає (номер рядка, рядок) для вже прив'язаного Telegram ID."""
    ws = get_players_sheet()
    for i, row in enumerate(ws.get_all_values()[1:], 2):
        if len(row) > 3 and row[3].strip() == str(telegram_id):
            return i, row
    return None, None


def _ensure_player_auxiliary_rows(nick: str):
    """Створює відсутні рядки балансу та прогресу для нового гравця."""
    sh = get_spreadsheet()
    balance = sh.worksheet("Авто-баланс🤑")
    rows = balance.get_all_values()
    if not any(row and row[0].strip().casefold() == nick.casefold() for row in rows[1:]):
        row_no = len(rows) + 1
        balance.append_row([
            nick,
            f"=SUMIF('Нарахування💰'!B:B;A{row_no};'Нарахування💰'!I:I)+SUMIF('Ставки🎯'!B:B;A{row_no};'Ставки🎯'!H:H)",
            f"=SUMIF('Покупки🛒'!B:B;A{row_no};'Покупки🛒'!D:D)",
            f"=B{row_no}-C{row_no}",
        ], value_input_option="USER_ENTERED")

    progress = sh.worksheet("Прогрес📊")
    progress_rows = progress.get_all_values()
    if not any(row and row[0].strip().casefold() == nick.casefold() for row in progress_rows[1:]):
        progress.append_row([nick, "0", "Немає даних", "Новачок"], value_input_option="RAW")


def register_or_bind_player(nick: str, telegram_id: int):
    """Прив'язує існуючий нік або створює нового, не дозволяючи забрати чужий нік."""
    nick = " ".join((nick or "").strip().split())
    if len(nick) < 2 or len(nick) > 32:
        raise ValueError("Нік має містити від 2 до 32 символів.")
    if any(ord(ch) < 32 for ch in nick):
        raise ValueError("Нік містить неприпустимі символи.")

    ws = get_players_sheet()
    rows = ws.get_all_values()

    # Один Telegram ID не може отримати другий нік через самореєстрацію.
    for row in rows[1:]:
        if len(row) > 3 and row[3].strip() == str(telegram_id):
            current_nick = row[1].strip() if len(row) > 1 else ""
            if current_nick.casefold() == nick.casefold():
                return "already_linked", current_nick
            raise ValueError(f"Твій Telegram уже прив'язаний до ніку «{current_nick}». Якщо потрібно змінити нік, звернись до організатора.")

    # Існуючий нік зберігає свій рядок, історію та прогрес.
    for i, row in enumerate(rows[1:], 2):
        existing_nick = row[1].strip() if len(row) > 1 else ""
        if existing_nick and existing_nick.casefold() == nick.casefold():
            existing_id = row[3].strip() if len(row) > 3 else ""
            if existing_id and existing_id != str(telegram_id):
                raise ValueError("Цей нік уже прив'язаний до іншого Telegram-акаунта. Перевір написання або звернись до організатора.")
            if not existing_id:
                bind_telegram_id(i, telegram_id)
            _ensure_player_auxiliary_rows(existing_nick)
            return ("linked_existing" if not existing_id else "already_linked", existing_nick)

    # Новий гравець додається в кінець таблиці — існуючі рядки не змінюються.
    ws.append_row(["", nick, "", str(telegram_id)], value_input_option="RAW")
    _ensure_player_auxiliary_rows(nick)
    return "created", nick


def add_purchase(nick: str, item_name: str, price: int):
    # Додає рядок покупки
    get_spreadsheet().worksheet("Покупки🛒").append_row([
        datetime.now().strftime("%d.%m.%Y"), nick, item_name, price
    ])

def get_market_items():
    # Використовується кешем для початкового завантаження
    rows = get_spreadsheet().worksheet("🖤MafMarket🖤").get_all_values()
    return [{"name": r[0], "price": r[1], "description": r[2], "level": r[3]} for r in rows[2:] if r and r[0]]

