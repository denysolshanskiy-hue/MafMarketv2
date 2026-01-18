import gspread
import os
from google.oauth2.service_account import Credentials
from datetime import datetime

SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

def get_client():
    raw_pk = os.getenv("G_PRIVATE_KEY", "")
    
    # Видаляємо пробіли та зайві лапки
    clean_pk = raw_pk.strip().strip('"').strip("'")
    
    # Виправляємо злипання заголовка з тілом ключа
    header = "-----BEGIN PRIVATE KEY-----"
    if clean_pk.startswith(header) and not clean_pk.startswith(header + "\\n") and not clean_pk.startswith(header + "\n"):
        clean_pk = clean_pk.replace(header, header + "\\n")
        
    # Тепер замінюємо текстові \n на реальні переноси
    if "\\n" in clean_pk:
        clean_pk = clean_pk.replace("\\n", "\n")

    creds_dict = {
        "type": "service_account",
        "project_id": os.getenv("G_PROJECT_ID"),
        "private_key_id": os.getenv("G_PRIVATE_KEY_ID"),
        "private_key": clean_pk,
        "client_email": os.getenv("G_CLIENT_EMAIL"),
        "client_id": os.getenv("G_CLIENT_ID"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": os.getenv("G_CERT_URL")
    }

    creds = Credentials.from_service_account_info(creds_dict).with_scopes(SCOPES)
    return gspread.authorize(creds)
    
def get_spreadsheet():
    return get_client().open(os.getenv("SPREADSHEET_NAME"))

# ... решта функцій (get_players_sheet і т.д.) залишаються без змін ...

def get_players_sheet():
    return get_spreadsheet().worksheet("Гравці🕵️‍♂️")

def find_player_by_nick(nick: str):
    ws = get_players_sheet()
    rows = ws.get_all_values()
    for i, row in enumerate(rows[1:], 2):
        if len(row) > 1 and row[1].strip().lower() == nick.strip().lower():
            return i, row
    return None, None

def bind_telegram_id(row_index: int, telegram_id: int):
    try:
        ws = get_players_sheet()
        print(f"Спроба запису ID {telegram_id} у рядок {row_index}, стовпчик 4")
        ws.update_cell(row_index, 4, str(telegram_id))
        print("Запис успішний!")
    except Exception as e:
        print(f"Помилка при записі в таблицю: {e}")

def get_balance_by_telegram_id(telegram_id: int):
    players = get_players_sheet().get_all_values()
    nick = next((r[1] for r in players[1:] if len(r) > 3 and str(r[3]) == str(telegram_id)), None)
    if not nick: return None

    rows = get_spreadsheet().worksheet("Авто-баланс🤑").get_all_values()
    for r in rows[1:]:
        if r[0].strip().lower() == nick.strip().lower():
            # Захист від пустих клітинок та ком замість точок
            def to_int(val):
                try: return int(float(str(val).replace(',', '.')))
                except: return 0
            return {"nick": r[0], "total": to_int(r[3]), "spent": to_int(r[2])}
    return None

def get_market_items():
    rows = get_spreadsheet().worksheet("🖤MafMarket🖤").get_all_values()
    return [{"name": r[0], "price": r[1], "description": r[2], "level": r[3]} for r in rows[2:] if r[0]]

def add_purchase(nick: str, item_name: str, price: int):
    get_spreadsheet().worksheet("Покупки🛒").append_row([datetime.now().strftime("%d.%m.%Y"), nick, item_name, price])

def get_progress_by_nick(nick: str):
    rows = get_spreadsheet().worksheet("Прогрес📊").get_all_values()
    for r in rows[1:]:
        if r[0].strip().lower() == nick.strip().lower():
            return {"nick": r[0], "balance": r[1], "available": r[2], "status": r[3]}
    return None

def get_next_goal(balance: int):
    for item in get_market_items():
        try:
            price = int(item["price"])
            if balance < price:
                return {"name": item["name"], "price": price, "remaining": price - balance}
        except: continue
    return None
def get_market_item_by_name(name: str):
    items = get_market_items()
    search = name.strip().lower()
    return next((i for i in items if i['name'].strip().lower() == search), None)






