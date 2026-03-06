print("=== BOT FILE LOADED ===")
import os
import traceback
import sys
import time
import asyncio
from threading import Thread
from flask import Flask
from dotenv import load_dotenv

# Завантажуємо змінні оточення
if os.path.exists(".env"):
    load_dotenv()

# ---------- WEB SERVER FOR KOYEB ----------
flask_app = Flask('')

@flask_app.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    flask_app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()

# ---------- IMPORTS ----------
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# Імпортуємо функції
from sheets import (
    get_spreadsheet,
    find_player_by_nick,
    bind_telegram_id,
    get_market_items,
    add_purchase
)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_TELEGRAM_IDS = {444726017}  # ваш Telegram ID

def is_admin(update: Update) -> bool:
    return update.effective_user.id in ADMIN_TELEGRAM_IDS

# ---------- GLOBAL CACHING SYSTEM ----------
class BotCache:
    def __init__(self):
        self.market_items = None
        self.players_list = None
        self.balance_data = None
        self.progress_data = None
        self.last_update = 0
        self.ttl = 3600  # 1 година

    def is_expired(self):
        # Якщо даних взагалі немає — вважаємо, що вони прострочені
        if self.market_items is None: return True
        return (time.time() - self.last_update) > self.ttl

    async def update(self, force=False):
        # Оновлюємо ТІЛЬКИ якщо форсовано або час вийшов
        if force or self.is_expired():
            print("🚀 Глобальне оновлення кешу з Google Sheets...")
            try:
                sh = get_spreadsheet()
                # Робимо запити один за одним, але зберігаємо в пам'ять
                self.market_items = get_market_items()
                self.players_list = sh.worksheet("Гравці🕵️‍♂️").get_all_values()
                self.balance_data = sh.worksheet("Авто-баланс🤑").get_all_values()
                self.progress_data = sh.worksheet("Прогрес📊").get_all_values()
                
                self.last_update = time.time()
                print(f"✅ Кеш оновлено! Наступне авто-оновлення через годину.")
            except Exception as e:
                print(f"❌ Помилка оновлення кешу: {e}")

cache = BotCache()

# ---------- Вспоміжні функції для пошуку в кеші ----------
def get_user_nick_from_cache(user_id):
    if not cache.players_list: return None
    user_id_str = str(user_id)
    # Шукаємо в 4-му стовпчику (індекс 3)
    for row in cache.players_list[1:]:
        if len(row) > 3 and str(row[3]) == user_id_str:
            return row[1]
    return None

# ---------- MENU ----------
def get_menu(is_admin_user=False):
    keyboard = [
        ["🧾 Мій баланс", "🛒 Магазин"],
        ["📈 Мій прогрес"],
    ]

    if is_admin_user:
        keyboard.append(["📢 Повідомити про нарахування"])

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True
    )

# ---------- COMMANDS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cache.update() # Підвантажуємо дані при старті
    await update.message.reply_text(
        "Вітаю в MafMarket 🖤\n\nВведіть свій нік, щоб привʼязати акаунт:"
    )
    context.user_data["awaiting_nick"] = True

async def refresh_cache_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = os.getenv("ADMIN_TELEGRAM_ID")
    if admin_id and update.effective_user.id == int(admin_id):
        await update.message.reply_text("⏳ Оновлюю всі дані з таблиць...")
        await cache.update(force=True)
        await update.message.reply_text("✅ Дані синхронізовано!")
    else:
        await update.message.reply_text("❌ Відмовлено в доступі.")

# ---------- AUTH ----------
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_nick"): return

    try:
        nick = update.message.text.strip()
        row_index, player = find_player_by_nick(nick)

        if not player:
            await update.message.reply_text("❌ Такий нік не знайдено.")
            return

        bind_telegram_id(row_index, update.effective_user.id)
        context.user_data["awaiting_nick"] = False
        
        await cache.update(force=True) # Оновлюємо кеш після реєстрації
        await update.message.reply_text(f"✅ Вітаю, {nick}! Акаунт привʼязано.", reply_markup=get_menu(is_admin(update)))

    except Exception as e:
        await update.message.reply_text(f"⚠️ Помилка: {e}")


# ---------- BALANCE (МИТТЄВО) ----------
async def my_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Якщо даних немає взагалі - завантажуємо, інакше - використовуємо що є
    if cache.balance_data is None:
        await update.message.reply_text("⏳ Завантажую дані (лише раз)...")
        await cache.update()

    user_id = update.effective_user.id
    nick = get_user_nick_from_cache(user_id)
    
    if not nick:
        await update.message.reply_text("❌ Спочатку напишіть свій нік боту для прив'язки.")
        return

    search_nick = nick.strip().lower()
    user_row = next((r for r in cache.balance_data[1:] if r and r[0].strip().lower() == search_nick), None)

    if not user_row:
        await update.message.reply_text("❌ Дані балансу ще не згенеровані в таблиці.")
        return

    def to_int(val):
        try: return int(float(str(val).replace(',', '.')))
        except: return 0

    total = to_int(user_row[3]) if len(user_row) > 3 else 0
    spent = to_int(user_row[2]) if len(user_row) > 2 else 0

    await update.message.reply_text(
        f"🧾 *Ваш баланс*\n\n👤 {nick}\n💰 Баланс МК: *{total}*\n💸 Витрачено: {spent}",
        parse_mode="Markdown",
    )

# ---------- MARKET (FAST) ----------
async def show_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cache.update()
    items = cache.market_items
    if not items:
        await update.message.reply_text("❌ Магазин порожній.")
        return

    text = "🛒 *Магазин MafMarket*\n\n"
    for item in items:
        text += f"🧾 *{item['name']}*\n💰 {item['price']} МК\n✨ {item['description']}\n🔓 {item['level']}\n\n"

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🛍 Купити", callback_data="open_buy_menu")]])
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)

async def open_buy_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await cache.update()
    
    keyboard = [[InlineKeyboardButton(f"{i['name']} — {i['price']} МК", callback_data=f"buy:{i['name']}")] for i in cache.market_items]
    await query.message.edit_text("🛒 Оберіть товар:", reply_markup=InlineKeyboardMarkup(keyboard))

async def process_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    item_name = query.data.split(":", 1)[1]
    item = next((i for i in cache.market_items if i['name'] == item_name), None)
    nick = get_user_nick_from_cache(query.from_user.id)

    if not item or not nick:
        await query.message.reply_text("❌ Помилка: товар або юзер не знайдені.")
        return

    # Перевірка балансу в поточному кеші
    balance_val = 0
    for r in cache.balance_data[1:]:
        if r[0].strip().lower() == nick.strip().lower():
            try: balance_val = int(float(str(r[3]).replace(',', '.')))
            except: pass
            break

    price = int(item["price"])
    if balance_val < price:
        await query.message.reply_text(f"❌ Недостатньо МК. У вас: {balance_val}")
        return

    # 1. Відправляємо покупку в таблицю
    add_purchase(nick, item["name"], price)
    
    # 2. ОНОВЛЮЄМО КЕШ (це ключовий момент)
    # Поки Google Sheets обробляє запис, ми просимо бота перекачати дані
    await query.message.reply_text("⏳ Обробка покупки та оновлення балансу...")
    await cache.update(force=True) 

    # 3. Повідомляємо про успіх
    await query.message.edit_text(
        f"✅ *Покупка успішна!*\n\n"
        f"📦 Товар: {item['name']}\n"
        f"💰 Списано: {price} МК\n"
        f"📉 Ваш новий баланс оновлено!",
        parse_mode="Markdown"
    )

    # Сповіщення адміна (як і було)
    admin_id = os.getenv("ADMIN_TELEGRAM_ID")
    if admin_id:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"🛍 *Нова покупка!*\n👤 {nick}\n📦 *{item['name']}*\n💰 {price} МК",
                parse_mode="Markdown"
            )
        except: pass

# ---------- PROGRESS (FAST) ----------
async def my_progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if cache.progress_data is None:
        await cache.update()

    nick = get_user_nick_from_cache(update.effective_user.id)
    if not nick:
        await update.message.reply_text("❌ Нік не прив'язано.")
        return

    search_nick = nick.strip().lower()
    prog_row = None
    for r in cache.progress_data[1:]:
        if len(r) > 0 and r[0].strip().lower() == search_nick:
            prog_row = r
            break
    
    if not prog_row:
        await update.message.reply_text("❌ Дані прогресу відсутні.")
        return

    # Витягуємо дані з колонок Прогресу
    balance_val = prog_row[1] if len(prog_row) > 1 else "0"
    available = prog_row[2] if len(prog_row) > 2 else "Немає даних"
    status = prog_row[3] if len(prog_row) > 3 else "Новачок"

    text = (
        f"📈 *Ваш прогрес*\n\n"
        f"👤 Нік: {nick}\n"
        f"💰 Баланс: *{balance_val} МК*\n"
        f"🎯 Статус: *{status}*\n\n"
        f"🛒 Доступно для купівлі:\n{available}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

# ----------------NEWSLETTER------------
from sheets import get_players_sheet


async def notify_rewards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    ws = get_players_sheet()
    rows = ws.get_all_values()

    sent = 0

for i in range(1, len(rows)):
    telegram_id = rows[i][3]  # колонка D

    if telegram_id:
        try:
            await context.bot.send_message(
                chat_id=int(telegram_id),
                text=(
                    "🪙 Мафкоїни нараховано!\n\n"
                    "Перевірте свій баланс у MafMarket 🖤"
                ),
                parse_mode="HTML"
            )

            sent += 1

            await asyncio.sleep(0.05)

        except:
            pass

    await update.message.reply_text(f"Розіслано: {sent}")

app.add_handler(
    MessageHandler(
        filters.Regex("^📢 Повідомити про нарахування$"),
        notify_rewards
    )
)
# ---------- MAIN ----------
def main():
    if not TOKEN: return
    keep_alive()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("refresh", refresh_cache_command))
    app.add_handler(MessageHandler(filters.Regex("^🧾 Мій баланс$"), my_balance))
    app.add_handler(MessageHandler(filters.Regex("^🛒 Магазин$"), show_market))
    app.add_handler(MessageHandler(filters.Regex("^📈 Мій прогрес$"), my_progress))
    app.add_handler(CallbackQueryHandler(open_buy_menu, pattern="^open_buy_menu$"))
    app.add_handler(CallbackQueryHandler(process_buy, pattern="^buy:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.run_polling()

if __name__ == "__main__":
    main()




