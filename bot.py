print("=== BOT FILE LOADED ===")
import os
import traceback
import sys
from threading import Thread
from flask import Flask
from dotenv import load_dotenv

# Завантажуємо змінні оточення
if os.path.exists(".env"):
    load_dotenv()

# ---------- WEB SERVER FOR KOYEB (KEEP-ALIVE) ----------
flask_app = Flask('')

@flask_app.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    # Koyeb автоматично призначає порт 8080 або інший через змінну PORT
    port = int(os.environ.get('PORT', 8080))
    flask_app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()

# ---------- DIAGNOSTICS ----------
print("=== DIAGNOSTICS ===")
print(f"BOT_TOKEN exists: {os.getenv('BOT_TOKEN') is not None}")
print(f"SPREADSHEET_NAME: {os.getenv('SPREADSHEET_NAME')}")
print(f"ADMIN_ID exists: {os.getenv('ADMIN_TELEGRAM_ID') is not None}")
print("===================")

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

from sheets import (
    find_player_by_nick,
    bind_telegram_id,
    get_balance_by_telegram_id,
    get_market_items,
    add_purchase,
    get_market_item_by_name,
    get_progress_by_nick,
    get_next_goal
)

TOKEN = os.getenv("BOT_TOKEN")

# ---------- CACHING SYSTEM ----------
import time

class BotCache:
    def __init__(self):
        self.market_items = None
        self.players_data = None
        self.last_update = 0
        self.ttl = 3600  # Час життя кешу в секундах (1 година)

    def is_expired(self):
        return (time.time() - self.last_update) > self.ttl

    async def update(self, force=False):
        if force or self.market_items is None or self.is_expired():
            print("Refreshing cache from Google Sheets...")
            # Отримуємо всі дані пакетом (потрібно буде додати функцію в sheets.py)
            self.market_items = get_market_items() 
            # Для балансів краще залишити запит до БД або кешувати вибірково, 
            # але каталог товарів кешуємо обов'язково
            self.last_update = time.time()
            print("Cache updated successfully!")

cache = BotCache()

# ---------- MENU ----------
MENU = ReplyKeyboardMarkup(
    [
        ["🧾 Мій баланс", "🛒 Магазин"],
        ["📈 Мій прогрес"],
    ],
    resize_keyboard=True,
)

# ---------- COMMANDS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Вітаю в MafMarket 🖤\n\n"
        "Введіть свій нік, щоб привʼязати акаунт:"
    )
    context.user_data["awaiting_nick"] = True

# ---------- AUTH ----------
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_nick"):
        return

    try:
        nick = update.message.text.strip()
        print(f"DEBUG: Пошук ніку: {nick}")
        
        row_index, player = find_player_by_nick(nick)

        if not player:
            await update.message.reply_text("❌ Такий нік не знайдено.")
            return

        print(f"DEBUG: Нік знайдено в рядку {row_index}. Спроба прив'язки ID...")
        bind_telegram_id(row_index, update.effective_user.id)
        context.user_data["awaiting_nick"] = False

        await update.message.reply_text(
            f"✅ Акаунт привʼязано, {nick}!",
            reply_markup=MENU,
        )

    except Exception as e:
        print("!!! КРИТИЧНА ПОМИЛКА В HANDLE_TEXT !!!", file=sys.stderr)
        traceback.print_exc(file=sys.stderr) 
        sys.stderr.flush()
        await update.message.reply_text(f"⚠️ Технічна помилка: {e}")

# ---------- BALANCE ----------
async def my_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = get_balance_by_telegram_id(update.effective_user.id)
    if not data:
        await update.message.reply_text("❌ Баланс не знайдено.")
        return

    await update.message.reply_text(
        f"🧾 *Ваш баланс*\n\n"
        f"👤 {data['nick']}\n"
        f"💰 Баланс МК: *{data['total']}*\n"
        f"💸 Витрачено: {data['spent']}",
        parse_mode="Markdown",
    )

# ---------- MARKET ----------
async def show_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Перевіряємо кеш перед показом
    if cache.market_items is None or cache.is_expired():
        await update.message.reply_text("⏳ Завантажую дані з Google Sheets (це лише раз)...")
        await cache.update()

    items = cache.market_items # Беремо миттєво з пам'яті
    if not items:
        await update.message.reply_text("❌ Магазин порожній.")
        return

    text = "🛒 *Магазин MafMarket*\n\n"
    for item in items:
        text += (
            f"🧾 *{item['name']}*\n"
            f"💰 {item['price']} МК\n"
            f"✨ {item['description']}\n"
            f"🔓 {item['level']}\n\n"
        )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛍 Купити", callback_data="open_buy_menu")]
    ])

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=keyboard,
    )

# ---------- BUY PROCESS ----------
async def open_buy_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    items = get_market_items()
    keyboard = []
    for item in items:
        keyboard.append([
            InlineKeyboardButton(
                f"{item['name']} — {item['price']} МК",
                callback_data=f"buy:{item['name']}"
            )
        ])

    await query.message.reply_text(
        "🛒 Оберіть товар для покупки:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def process_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        item_name = query.data.split(":", 1)[1]
        item = get_market_item_by_name(item_name)
        
        if not item:
            await query.message.reply_text("❌ Товар не знайдено.")
            return

        balance = get_balance_by_telegram_id(query.from_user.id)
        if not balance:
            await query.message.reply_text("❌ Акаунт не знайдено.")
            return

        price = int(item["price"])
        if balance["total"] < price:
            await query.message.reply_text(
                f"❌ Бракує коштів.\nЦіна: {price} МК\nУ вас: {balance['total']} МК"
            )
            return

        # Записуємо покупку
        add_purchase(balance["nick"], item["name"], price)

        # Відповідь користувачу (Виправлено f-рядок)
        await query.message.reply_text(
            f"✅ *Покупка успішна!*\n\n"
            f"🧾 {item['name']}\n"
            f"💰 {price} МК\n\n"
            f"Очікуйте на отримання🥰",
            parse_mode="Markdown",
        )

        # Сповіщення адміна
        admin_id = os.getenv("ADMIN_TELEGRAM_ID")
        if admin_id:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"🛍 *Нова покупка в MafMarket!*\n\n"
                        f"👤 Покупець: {balance['nick']} (ID: {query.from_user.id})\n"
                        f"📦 Товар: *{item['name']}*\n"
                        f"💰 Ціна: {price} МК"
                    ),
                    parse_mode="Markdown"
                )
            except Exception as e:
                print(f"Admin Notify Error: {e}")

    except Exception as e:
        print("BUY ERROR:", e)
        traceback.print_exc()
        await query.message.reply_text("⚠️ Сталась технічна помилка під час покупки.")

async def refresh_cache_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) == os.getenv("ADMIN_TELEGRAM_ID"):
        await cache.update(force=True)
        await update.message.reply_text("✅ Кеш оновлено! Тепер бот бачить останні зміни з таблиці.")
    else:
        await update.message.reply_text("У вас немає прав для цієї команди.")

# ---------- MY PROGRESS ----------
async def my_progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    balance_data = get_balance_by_telegram_id(update.effective_user.id)
    if not balance_data:
        await update.message.reply_text("❌ Акаунт не знайдено.")
        return

    progress = get_progress_by_nick(balance_data["nick"])
    if not progress:
        await update.message.reply_text("❌ Прогрес не знайдено.")
        return

    next_goal = get_next_goal(int(balance_data["total"]))

    text = (
        f"📈 *Ваш прогрес*\n\n"
        f"👤 Нік: {progress['nick']}\n"
        f"💰 Баланс: *{progress['balance']} МК*\n\n"
        f"🛒 Доступно:\n{progress['available']}\n\n"
        f"🎯 Статус: *{progress['status']}*\n\n"
    )

    if next_goal:
        text += (
            f"➡️ *Наступна ціль:*\n"
            f"{next_goal['name']} — ще {next_goal['remaining']} МК"
        )
    else:
        text += "🏆 *Ви досягли максимального рівня!*"

    await update.message.reply_text(text, parse_mode="Markdown")

# ---------- MAIN ----------
def main():
    if not TOKEN:
        print("CRITICAL: No TOKEN found!")
        return

    # Запускаємо веб-сервер у фоновому потоці
    keep_alive()

    app = ApplicationBuilder().token(TOKEN).build()

    # Хендлери
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^🧾 Мій баланс$"), my_balance))
    app.add_handler(MessageHandler(filters.Regex("^🛒 Магазин$"), show_market))
    app.add_handler(MessageHandler(filters.Regex("^📈 Мій прогрес$"), my_progress))
    app.add_handler(CallbackQueryHandler(open_buy_menu, pattern="^open_buy_menu$"))
    app.add_handler(CallbackQueryHandler(process_buy, pattern="^buy:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CommandHandler("refresh", refresh_cache_command))
    print("Бот запущений як Web Service")
    app.run_polling()

if __name__ == "__main__":
    main()

