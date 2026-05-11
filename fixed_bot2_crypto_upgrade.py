# Telegram Casino Bot (Single File)
# Features:
# - Dice games (less/more/even/odd)
# - Balance system
# - Deposits via CryptoBot
# - Withdrawals via admin approval
# - Admin panel
# - Gift checks with wager requirements
# - SQLite database
# - Environment variables support

import os
import sqlite3
import random
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice
from aiogram.filters import CommandStart, Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiocryptopay import AioCryptoPay, Networks
import asyncio

# ===================== CONFIG =====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_NAME = "casino.db"

# ===== CryptoBot configuration =====
CRYPTO_NETWORK = os.getenv("CRYPTO_NETWORK", "MAIN_NET").upper()
CRYPTO_ASSET = os.getenv("CRYPTO_ASSET", "USDT")
MIN_DEPOSIT = float(os.getenv("MIN_DEPOSIT", "1"))
MAX_DEPOSIT = float(os.getenv("MAX_DEPOSIT", "100000"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env")

if not CRYPTOBOT_TOKEN:
    raise ValueError("CRYPTOBOT_TOKEN не найден в .env")

network_map = {
    "MAIN_NET": Networks.MAIN_NET,
    "TEST_NET": Networks.TEST_NET
}

selected_network = network_map.get(CRYPTO_NETWORK, Networks.MAIN_NET)

bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

crypto = AioCryptoPay(
    token=CRYPTOBOT_TOKEN,
    network=selected_network
)

logging.info(f"CryptoBot network: {CRYPTO_NETWORK}")
logging.info(f"Deposit asset: {CRYPTO_ASSET}")

# ===================== DATABASE =====================
# Enhanced UI + Auto deposit monitoring enabled
def db():
    return sqlite3.connect(DB_NAME)

def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        balance REAL DEFAULT 0,
        wager REAL DEFAULT 0,
        wager_progress REAL DEFAULT 0,
        username TEXT
    )''')

    cur.execute('''CREATE TABLE IF NOT EXISTS withdrawals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount REAL,
        wallet TEXT,
        status TEXT DEFAULT 'pending'
    )''')

    cur.execute('''CREATE TABLE IF NOT EXISTS checks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT,
        amount REAL,
        activations INTEGER,
        used INTEGER DEFAULT 0,
        wager REAL DEFAULT 0
    )''')

    cur.execute('''CREATE TABLE IF NOT EXISTS check_activations (
        user_id INTEGER,
        check_id INTEGER
    )''')

    conn.commit()
    conn.close()

init_db()

# ===================== VALIDATION =====================
def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ===================== HELPERS =====================
def get_user(user_id):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = cur.fetchone()
    if not user:
        cur.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
        cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        user = cur.fetchone()
    conn.close()
    return user

def update_balance(user_id, amount):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()

def add_wager(user_id, amount):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET wager = wager + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()

def update_wager_progress(user_id, amount):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET wager_progress = wager_progress + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()

def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Играть", callback_data="games")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")]
    ])

def games_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬇️ Куб меньше", callback_data="game_less")],
        [InlineKeyboardButton(text="⬆️ Куб больше", callback_data="game_more")],
        [InlineKeyboardButton(text="⚖️ Куб чет", callback_data="game_even")],
        [InlineKeyboardButton(text="🎯 Куб нечет", callback_data="game_odd")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back")]
    ])

# ===================== STATES =====================
class DepositState(StatesGroup):
    amount = State()

class WithdrawState(StatesGroup):
    amount = State()
    wallet = State()

class GameState(StatesGroup):
    amount = State()
    mode = State()

class AdminCheckState(StatesGroup):
    amount = State()
    activations = State()
    wager = State()

# ===================== START =====================
@dp.message(CommandStart())
async def start(message: types.Message):
    get_user(message.from_user.id)

    args = message.text.split()
    if len(args) > 1 and args[1].startswith("check_"):
        code = args[1].replace("check_", "")
        await activate_check(message, code)
        return

    welcome_text = (
        "🎰 <b>Добро пожаловать в Casino Bot</b>\n\n"
        "💸 Минимальная ставка: <b>$0.1</b>\n\n"
        "🎲 <b>Игры:</b>\n"
        "• Куб меньше (1-3) — x2\n"
        "• Куб больше (4-6) — x2\n"
        "• Куб чет — x2\n"
        "• Куб нечет — x2\n\n"
        "🏆 Испытай удачу прямо сейчас!"
    )

    await message.answer(
        welcome_text,
        parse_mode="HTML",
        reply_markup=main_menu()
    )

# ===================== PROFILE =====================
@dp.callback_query(F.data == "profile")
async def profile(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    balance = user[1]
    wager = user[2]
    progress = user[3]

    text = (
        f"👤 Ваш профиль\n\n"
        f"💰 Баланс: ${balance:.2f}\n"
        f"🎯 Вагер: ${wager:.2f}\n"
        f"📈 Отыгрыш: ${progress:.2f}/${wager:.2f}\n\n"
        f"👇 Управление балансом:"
    )

    profile_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Пополнить", callback_data="deposit")],
        [InlineKeyboardButton(text="💸 Вывести", callback_data="withdraw")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back")]
    ])

    await callback.message.edit_text(text, reply_markup=profile_kb)

# ===================== DEPOSIT =====================
pending_invoices = {}

@dp.callback_query(F.data == "deposit")
async def deposit(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "💳 <b>Пополнение баланса</b>\n\nВведите сумму пополнения в USDT:",
        parse_mode="HTML"
    )
    await state.set_state(DepositState.amount)

@dp.message(DepositState.amount)
async def process_deposit(message: types.Message, state: FSMContext):
    amount = safe_float(message.text)
    if amount is None or amount <= 0:
        await message.answer("Введите корректную сумму")
        return

    if amount < MIN_DEPOSIT:
        await message.answer(f"Минимальный депозит: {MIN_DEPOSIT}$")
        return

    if amount > MAX_DEPOSIT:
        await message.answer(f"Максимальный депозит: {MAX_DEPOSIT}$")
        return

    invoice = await crypto.create_invoice(
        asset=CRYPTO_ASSET,
        amount=round(amount, 2),
        description=f"Deposit for user {message.from_user.id}"
    )

    pending_invoices[invoice.invoice_id] = {
        "user_id": message.from_user.id,
        "amount": amount
    }

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить счет", url=invoice.bot_invoice_url)],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")]
    ])

    await message.answer(
        f"💰 <b>Счет создан</b>\n\nСумма: ${amount:.2f}\nПосле оплаты баланс обновится автоматически.",
        parse_mode="HTML",
        reply_markup=kb
    )
    await state.clear()

async def check_invoices():
    while True:
        try:
            invoices = await crypto.get_invoices(status='paid')
            for invoice in invoices.items:
                if invoice.invoice_id in pending_invoices:
                    data = pending_invoices.pop(invoice.invoice_id)
                    update_balance(data['user_id'], data['amount'])
                    await bot.send_message(
                        data['user_id'],
                        f"✅ Пополнение успешно зачислено!\n💰 Баланс пополнен на ${data['amount']:.2f}",
                        reply_markup=main_menu()
                    )
        except Exception as e:
            logging.error(f"Invoice check error: {e}")

        await asyncio.sleep(15)

# ===================== WITHDRAW =====================
@dp.callback_query(F.data == "withdraw")
async def withdraw(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите сумму вывода:")
    await state.set_state(WithdrawState.amount)

@dp.message(WithdrawState.amount)
async def withdraw_amount(message: types.Message, state: FSMContext):
    amount = safe_float(message.text)
    if amount is None or amount <= 0:
        await message.answer("Введите корректную сумму")
        return

    user = get_user(message.from_user.id)

    if user[1] < amount:
        await message.answer("Недостаточно средств")
        return

    if user[3] < user[2]:
        await message.answer("Вагер не отыгран")
        return

    await state.update_data(amount=amount)
    await message.answer("Введите ваш CryptoBot чек / кошелек:")
    await state.set_state(WithdrawState.wallet)

@dp.message(WithdrawState.wallet)
async def withdraw_wallet(message: types.Message, state: FSMContext):
    data = await state.get_data()
    amount = data['amount']
    wallet = message.text

    update_balance(message.from_user.id, -amount)

    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO withdrawals (user_id, amount, wallet) VALUES (?, ?, ?)",
        (message.from_user.id, amount, wallet)
    )
    withdrawal_id = cur.lastrowid
    conn.commit()
    conn.close()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"approve_{withdrawal_id}"),
            InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_{withdrawal_id}")
        ]
    ])

    await bot.send_message(
        ADMIN_ID,
        f"Новая заявка на вывод\nID: {withdrawal_id}\nПользователь: {message.from_user.id}\nСумма: ${amount}\nКошелек: {wallet}",
        reply_markup=kb
    )

    await message.answer("Заявка отправлена админу")
    await state.clear()

# ===================== ADMIN WITHDRAW =====================
@dp.callback_query(F.data.startswith("approve_"))
async def approve_withdraw(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return

    withdrawal_id = int(callback.data.split("_")[1])

    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE withdrawals SET status='approved' WHERE id=?", (withdrawal_id,))
    cur.execute("SELECT user_id FROM withdrawals WHERE id=?", (withdrawal_id,))
    user_id = cur.fetchone()[0]
    conn.commit()
    conn.close()

    await bot.send_message(user_id, "✅ Ваш вывод подтвержден")
    await callback.message.edit_text("Вывод подтвержден")

@dp.callback_query(F.data.startswith("cancel_"))
async def cancel_withdraw(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return

    withdrawal_id = int(callback.data.split("_")[1])

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id, amount FROM withdrawals WHERE id=?", (withdrawal_id,))
    row = cur.fetchone()

    if row:
        user_id, amount = row
        update_balance(user_id, amount)
        cur.execute("UPDATE withdrawals SET status='cancelled' WHERE id=?", (withdrawal_id,))
        conn.commit()
        await bot.send_message(user_id, "❌ Вывод отменен, деньги возвращены")

    conn.close()
    await callback.message.edit_text("Вывод отменен")

# ===================== GAMES =====================
@dp.callback_query(F.data == "games")
async def games(callback: types.CallbackQuery):
    await callback.message.edit_text("🎲 Выберите игру:", reply_markup=games_menu())

@dp.callback_query(F.data.startswith("game_"))
async def choose_game(callback: types.CallbackQuery, state: FSMContext):
    mode = callback.data.replace("game_", "")
    await state.update_data(mode=mode)
    await callback.message.answer("Введите ставку (от $0.1):")
    await state.set_state(GameState.amount)

@dp.message(GameState.amount)
async def play_game(message: types.Message, state: FSMContext):
    amount = safe_float(message.text)
    if amount is None:
        await message.answer("Введите корректную сумму")
        return

    if amount < 0.1:
        await message.answer("Минимальная ставка $0.1")
        return

    user = get_user(message.from_user.id)
    if user[1] < amount:
        await message.answer("Недостаточно средств")
        return

    data = await state.get_data()
    mode = data['mode']

    update_balance(message.from_user.id, -amount)
    roll = random.randint(1, 6)

    win = False

    if mode == "less" and roll in [1,2,3]:
        win = True
    elif mode == "more" and roll in [4,5,6]:
        win = True
    elif mode == "even" and roll in [2,4,6]:
        win = True
    elif mode == "odd" and roll in [1,3,5]:
        win = True

    update_wager_progress(message.from_user.id, amount)

    if win:
        winnings = amount * 2
        update_balance(message.from_user.id, winnings)
        result = f"🎉 Выпало {roll}\nВы выиграли ${winnings:.2f}"
    else:
        result = f"💀 Выпало {roll}\nВы проиграли ${amount:.2f}"

    await message.answer(result, reply_markup=main_menu())
    await state.clear()

# ===================== CHECKS =====================
@dp.message(Command("createcheck"))
async def create_check(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    await message.answer("Введите сумму чека:")
    await state.set_state(AdminCheckState.amount)

@dp.message(AdminCheckState.amount)
async def check_amount(message: types.Message, state: FSMContext):
    amount = safe_float(message.text)
    if amount is None or amount <= 0:
        await message.answer("Введите корректную сумму")
        return

    await state.update_data(amount=amount)
    await message.answer("Введите количество активаций:")
    await state.set_state(AdminCheckState.activations)

@dp.message(AdminCheckState.activations)
async def check_activations(message: types.Message, state: FSMContext):
    try:
        activations = int(message.text)
    except ValueError:
        await message.answer("Введите целое число")
        return

    if activations <= 0:
        await message.answer("Количество активаций должно быть больше 0")
        return

    await state.update_data(activations=activations)
    await message.answer("Введите вагер:")
    await state.set_state(AdminCheckState.wager)

@dp.message(AdminCheckState.wager)
async def check_wager(message: types.Message, state: FSMContext):
    data = await state.get_data()
    amount = data['amount']
    activations = data['activations']
    wager = safe_float(message.text)
    if wager is None or wager < 0:
        await message.answer("Введите корректный вагер")
        return

    code = f"CHK{random.randint(100000,999999)}"

    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO checks (code, amount, activations, wager) VALUES (?, ?, ?, ?)",
        (code, amount, activations, wager)
    )
    conn.commit()
    conn.close()

    link = f"https://t.me/{(await bot.me()).username}?start=check_{code}"

    await message.answer(f"✅ Чек создан:\n{link}")
    await state.clear()

async def activate_check(message, code):
    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT id, amount, activations, used, wager FROM checks WHERE code=?", (code,))
    check = cur.fetchone()

    if not check:
        await message.answer("Чек не найден")
        return

    check_id, amount, activations, used, wager = check

    if used >= activations:
        await message.answer("Чек закончился")
        return

    cur.execute(
        "SELECT * FROM check_activations WHERE user_id=? AND check_id=?",
        (message.from_user.id, check_id)
    )

    if cur.fetchone():
        await message.answer("Вы уже активировали чек")
        return

    update_balance(message.from_user.id, amount)
    add_wager(message.from_user.id, wager)

    cur.execute("UPDATE checks SET used = used + 1 WHERE id=?", (check_id,))
    cur.execute(
        "INSERT INTO check_activations (user_id, check_id) VALUES (?, ?)",
        (message.from_user.id, check_id)
    )

    conn.commit()
    conn.close()

    await message.answer(
        f"🎁 Вы получили ${amount:.2f}\n🎯 Вагер: ${wager:.2f}"
    )

# ===================== BACK =====================
@dp.callback_query(F.data == "back")
async def back(callback: types.CallbackQuery):
    await callback.message.edit_text("Главное меню", reply_markup=main_menu())

# ===================== RUN =====================
async def main():
    asyncio.create_task(check_invoices())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
