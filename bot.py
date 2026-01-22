print("=== BOT FILE LOADED ===")
import os
import traceback
import sys
import time
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
class BotCache:
    def __init__(self):
        self.market_items = None
        self.last_update = 0
        self.ttl = 3600  # Кеш житиме 1 годину

    def is_expired(self):
        return (time.time() - self.last_update) > self.ttl

    async def update(self, force=False):
        if force or self.market_items is None or self.is_expired():
            print("🚀 Refreshing market cache from Google Sheets...")
            # Отримуємо товари один раз пакетом
            self.market_items = get_market_items() 
            self.last_update = time.time()
            print(f"✅ Cache updated! Items found: {len(self.market_items) if self.market_items else 0}")

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
        "Вітаю в MafMarket 🖤\n\nВведіть свій нік, щоб привʼязати акаунт:"
    )
    context.user_data["awaiting_nick"] = True

# ---------- ADMIN COMMANDS ----------
async def refresh_cache_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = os.getenv("ADMIN_TELEGRAM_ID")
    if admin_id and update.effective_user.id == int(admin_id):
        await update.message.reply_text("⏳ Оновлюю кеш товарів...")
        await cache.update(force=True)
        await update.message.reply_text("✅ Кеш оновлено! Тепер магазин працює на нових даних.")
    else:
        await update.message.reply_text("❌ У вас немає прав для цієї команди.")

# ---------- AUTH ----------
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_nick"):
        return

    try:
        nick = update.message.text.strip()
        row_index, player = find_player_by_nick(nick)

        if not player:
            await update.message.reply_text("❌ Такий нік не знайдено.")
            return

        bind_telegram_id(row_index, update.effective_user.id)
        context.user_data["awaiting_nick"] = False
        await update.message.reply_text(f"✅ Акаунт привʼязано, {nick}!", reply_markup=MENU)

    except Exception as e:
        traceback.print_exc()
        await update.message.reply_text(f"⚠️ Помилка: {e}")

# ---------- BALANCE ----------
async def my_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Тут швидкість залежить від sheets.py
    data = get_balance_by_telegram_id(update.effective_user.id)
    if not data:
        await update.message.reply_text("❌ Баланс не знайдено. Переконайтеся, що ви прив'язали нік.")
        return

    await update.message.reply_text(
        f"🧾 *Ваш баланс*\n\n👤 {data['nick']}\n💰 Баланс МК: *{data['total']}*\n💸 Витрачено: {data['spent']}",
        parse_mode="Markdown",
    )

# ---------- MARKET ----------
async def show_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # МИТТЄВА ПЕРЕВІРКА КЕШУ
    if cache.market_items is None or cache.is_expired():
        await cache.update()

    items = cache.market_items
    if not items:
        await update.message.reply_text("❌ Магазин поки порожній.")
        return

    text = "🛒 *Магазин MafMarket*\n\n"
    for item in items:
        text += (
            f"🧾 *{item['name']}*\n"
            f"💰 {item['price']} МК\n"
            f"✨ {item['description']}\n"
            f"🔓 {item['level']}\n\n"
        )

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🛍 Купити", callback_data="open_buy_menu")]])
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)

# ---------- BUY PROCESS ----------
async def open_buy_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Беремо товари з кешу - МИТТЄВО
    if cache.market_items is None:
        await cache.update()
    
    items = cache.market_items
    keyboard = [[InlineKeyboardButton(f"{i['name']} — {i['price']} МК", callback_data=f"buy:{i['name']}")] for i in items]

    await query.message.edit_text("🛒 Оберіть товар для покупки:", reply_markup=InlineKeyboardMarkup(keyboard))

async def process_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        item_name = query.data.split(":", 1)[1]
        
        # Шукаємо товар у кеші замість запиту до Google
        item = next((i for i in cache.market_items if i['name'] == item_name), None) if cache.market_items else None
        
        # Якщо в кеші немає, пробуємо прямий запит (про всяк випадок)
        if not item:
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
            await query.message.reply_text(f"❌ Бракує коштів.\nЦіна: {price} МК\nУ вас: {balance['total']} МК")
            return

        add_purchase(balance["nick"], item["name"], price)

        await query.message.reply_text(
            f"✅ *Покупка успішна!*\n\n🧾 {item['name']}\n💰 {price} МК\n\nОчікуйте на отримання🥰",
            parse_mode="Markdown"
        )

        # Сповіщення адміна
        admin_id = os.getenv("ADMIN_TELEGRAM_ID")
        if admin_id:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"🛍 *Нова покупка!*\n👤 {balance['nick']}\n📦 *{item['name']}*\n💰 {price} МК",
                    parse_mode="Markdown"
                )
            except: pass

    except Exception as e:
        traceback.print_exc()
        await query.message.reply_text("⚠️ Технічна помилка при покупці.")

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
        f"📈 *Ваш прогрес*\n\n👤 Нік: {progress['nick']}\n"
        f"💰 Баланс: *{progress['balance']} МК*\n\n"
        f"🛒 Доступно:\n{progress['available']}\n\n"
        f"🎯 Статус: *{progress['status']}*\n\n"
    )
    if next_goal:
        text += f"➡️ *Наступна ціль:*\n{next_goal['name']} — ще {next_goal['remaining']} МК"
    else:
        text += "🏆 *Ви досягли максимального рівня!*"

    await update.message.reply_text(text, parse_mode="Markdown")

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

    print("🚀 MafMarket Bot (Optimized) is running on Koyeb...")
    app.run_polling()

if __name__ == "__main__":
    main()
