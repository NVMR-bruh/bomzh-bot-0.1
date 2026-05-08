#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import logging
import random
import secrets
import re
import html as html_mod
from datetime import datetime, timedelta

import aiosqlite
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.filters import Command, StateFilter, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ------------------------------------------------------------
# Логирование
# ------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------
# КОНФИГУРАЦИЯ
# ------------------------------------------------------------
BOT_TOKEN = "Ваш токен бота"
ADMIN_ID = 123445678 #введите ваш id tg
DB_PATH = "game.db"

# ------------------------------------------------------------
# ИГРОВЫЕ ПАРАМЕТРЫ
# ------------------------------------------------------------
WORK_TYPES = {
    "taxi":        {"name": "🚕 Таксист",     "income": 200,  "cd_min": 5,   "rounds": 1},
    "electric":    {"name": "🔌 Электрик",    "income": 350,  "cd_min": 7,   "rounds": 2},
    "freelance":   {"name": "💻 Фрилансер",   "income": 800,  "cd_min": 30,  "rounds": 4},
    "builder":     {"name": "🏗 Строитель",   "income": 1200, "cd_min": 60,  "rounds": 6},
    "cook":        {"name": "👨‍🍳 Повар",      "income": 500,  "cd_min": 10,  "rounds": 3},
    "programmer":  {"name": "🧑‍💻 Программист","income": 1000, "cd_min": 40,  "rounds": 5},
    "police":      {"name": "👮 Полицейский", "income": 1500, "cd_min": 120, "rounds": 7},
    "pilot":       {"name": "✈️ Пилот",      "income": 2000, "cd_min": 180, "rounds": 8},
}

BUSINESS_TYPES = {
    "lafka":       {"name": "🏪 Лавка",    "price": 10_000,           "income": 200},
    "bar":         {"name": "🍺 Бар",      "price": 25_000,           "income": 500},
    "club":        {"name": "🎵 Клуб",     "price": 100_000,          "income": 2500},
    "casino_pro":  {"name": "🏨 Казино",   "price": 1_000_000_000,    "income": 0},  # доход через хранилище
}

COLLECT_CD_HOURS = 6
CASINO_CD_MINUTES = 10
FORTUNE_CD_HOURS = 24
DUEL_TIMEOUT = 30

# ------------------------------------------------------------
# КЛАВИАТУРЫ
# ------------------------------------------------------------
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="💼 Работа")],
        [KeyboardButton(text="🏢 Бизнесы"), KeyboardButton(text="🎁 Бонус")],
        [KeyboardButton(text="🎰 Казино"), KeyboardButton(text="🤝 Взаимодействия")],
        [KeyboardButton(text="🏆 Топ игроков"), KeyboardButton(text="🔨 Аукцион")],
    ],
    resize_keyboard=True,
)

# ------------------------------------------------------------
# Глобальное соединение с БД
# ------------------------------------------------------------
db: aiosqlite.Connection = None

# ------------------------------------------------------------
# FSM состояния
# ------------------------------------------------------------
class Registration(StatesGroup):
    waiting_for_name = State()

class Transfer(StatesGroup):
    waiting_for_id = State()
    waiting_for_amount = State()

class Blackjack(StatesGroup):
    playing = State()

class WorkSession(StatesGroup):
    waiting_for_answer = State()

class AuctionCreate(StatesGroup):
    waiting_for_price = State()
    waiting_for_duration = State()

# ------------------------------------------------------------
# РАБОТА С БД
# ------------------------------------------------------------
async def ensure_db_schema():
    await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance INTEGER DEFAULT 1000,
            last_work TIMESTAMP,
            last_bonus TIMESTAMP,
            last_collect TIMESTAMP,
            game_id INTEGER,
            ref_code TEXT,
            referred_by INTEGER
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS businesses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            biz_type TEXT,
            name TEXT,
            income_per_hour INTEGER,
            price INTEGER,
            purchase_date TIMESTAMP,
            level INTEGER DEFAULT 1,
            supply_until TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS work_cooldowns (
            user_id INTEGER, work_type TEXT, last_used TIMESTAMP,
            PRIMARY KEY (user_id, work_type)
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS casino_cooldowns (
            user_id INTEGER PRIMARY KEY, last_used TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS fortune_cooldowns (
            user_id INTEGER PRIMARY KEY, last_used TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS banned_users (
            user_id INTEGER PRIMARY KEY, reason TEXT, ban_end TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS boosts (
            type TEXT PRIMARY KEY, multiplier REAL, expires TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY, amount INTEGER, activations_left INTEGER, expires TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS promo_uses (
            user_id INTEGER, code TEXT,
            PRIMARY KEY (user_id, code)
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS biz_limits (
            biz_type TEXT PRIMARY KEY, max_count INTEGER DEFAULT 3
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS duel_queue (
            user_id INTEGER, bet INTEGER, timestamp TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS auctions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER,
            seller_id INTEGER,
            start_price INTEGER,
            current_bid INTEGER,
            current_bidder_id INTEGER,
            end_time TIMESTAMP,
            active INTEGER DEFAULT 1
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS casino_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            min_bet_dice INTEGER DEFAULT 100,
            min_bet_roulette INTEGER DEFAULT 100,
            min_bet_blackjack INTEGER DEFAULT 100,
            min_bet_fortune INTEGER DEFAULT 1000,
            dice_win_chance REAL DEFAULT 0.1,
            roulette_win_chance REAL DEFAULT 0.5,
            blackjack_win_multiplier REAL DEFAULT 2.0,
            vault INTEGER DEFAULT 0
        )
    """)
    await db.execute("INSERT OR IGNORE INTO casino_settings (id) VALUES (1)")
    for bt in BUSINESS_TYPES:
        await db.execute("INSERT OR IGNORE INTO biz_limits (biz_type, max_count) VALUES (?, 3)", (bt,))
    # Миграции
    cur = await db.execute("PRAGMA table_info(users)")
    cols = [c[1] for c in await cur.fetchall()]
    if 'game_id' not in cols:
        await db.execute("ALTER TABLE users ADD COLUMN game_id INTEGER")
        await db.execute("UPDATE users SET game_id=0 WHERE user_id=?", (ADMIN_ID,))
        async with db.execute("SELECT user_id FROM users WHERE user_id!=? ORDER BY user_id", (ADMIN_ID,)) as c:
            rows = await c.fetchall()
            for i, (uid,) in enumerate(rows, 1):
                await db.execute("UPDATE users SET game_id=? WHERE user_id=?", (i, uid))
    if 'ref_code' not in cols:
        await db.execute("ALTER TABLE users ADD COLUMN ref_code TEXT")
    if 'referred_by' not in cols:
        await db.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER")
    cur = await db.execute("PRAGMA table_info(businesses)")
    cols = [c[1] for c in await cur.fetchall()]
    if 'biz_type' not in cols:
        await db.execute("ALTER TABLE businesses ADD COLUMN biz_type TEXT")
        await db.execute("ALTER TABLE businesses ADD COLUMN level INTEGER DEFAULT 1")
        await db.execute("ALTER TABLE businesses ADD COLUMN supply_until TIMESTAMP")
    await db.commit()

# ------------------------------------------------------------
# Вспомогательные функции
# ------------------------------------------------------------
async def get_next_game_id():
    async with db.execute("SELECT COALESCE(MAX(game_id),0) FROM users WHERE game_id IS NOT NULL") as c:
        return (await c.fetchone())[0] + 1

async def register_user(user_id, username, referred_by=None):
    gid = 0 if user_id == ADMIN_ID else await get_next_game_id()
    ref_code = secrets.token_hex(4)
    await db.execute(
        "INSERT OR IGNORE INTO users (user_id, username, balance, game_id, ref_code, referred_by) VALUES (?,?,1000,?,?,?)",
        (user_id, username, gid, ref_code, referred_by)
    )
    await db.commit()
    if referred_by:
        await update_balance(user_id, 10000)
        inviter_tg = await get_user_by_game_id(referred_by)
        if inviter_tg:
            await update_balance(inviter_tg, 5000)

async def get_user(user_id):
    async with db.execute("SELECT user_id, username, balance, last_bonus, last_collect, game_id, ref_code FROM users WHERE user_id=?", (user_id,)) as c:
        return await c.fetchone()

async def get_user_by_game_id(game_id: int):
    async with db.execute("SELECT user_id FROM users WHERE game_id=?", (game_id,)) as c:
        row = await c.fetchone()
        return row[0] if row else None

async def update_balance(user_id, amount):
    await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, user_id))
    await db.commit()

async def set_last_bonus(user_id, ts):
    await db.execute("UPDATE users SET last_bonus=? WHERE user_id=?", (ts, user_id))
    await db.commit()

async def set_last_collect(user_id, ts):
    await db.execute("UPDATE users SET last_collect=? WHERE user_id=?", (ts, user_id))
    await db.commit()

async def add_business(user_id, biz_type, name, income, price):
    now = datetime.now().isoformat()
    await db.execute(
        "INSERT INTO businesses (user_id, biz_type, name, income_per_hour, price, purchase_date, supply_until) VALUES (?,?,?,?,?,?,?)",
        (user_id, biz_type, name, income, price, now, now)
    )
    await db.commit()

async def get_businesses(user_id):
    async with db.execute(
        "SELECT id, biz_type, name, income_per_hour, price, level, supply_until, purchase_date FROM businesses WHERE user_id=?",
        (user_id,)
    ) as c:
        return await c.fetchall()

async def get_work_cooldown(user_id, work_type):
    async with db.execute("SELECT last_used FROM work_cooldowns WHERE user_id=? AND work_type=?", (user_id, work_type)) as c:
        row = await c.fetchone()
        return row[0] if row else None

async def set_work_cooldown(user_id, work_type, ts):
    await db.execute("INSERT OR REPLACE INTO work_cooldowns VALUES (?,?,?)", (user_id, work_type, ts))
    await db.commit()

async def get_casino_cd(user_id):
    async with db.execute("SELECT last_used FROM casino_cooldowns WHERE user_id=?", (user_id,)) as c:
        row = await c.fetchone()
        return row[0] if row else None

async def set_casino_cd(user_id, ts):
    await db.execute("INSERT OR REPLACE INTO casino_cooldowns VALUES (?,?)", (user_id, ts))
    await db.commit()

async def get_fortune_cd(user_id):
    async with db.execute("SELECT last_used FROM fortune_cooldowns WHERE user_id=?", (user_id,)) as c:
        row = await c.fetchone()
        return row[0] if row else None

async def set_fortune_cd(user_id, ts):
    await db.execute("INSERT OR REPLACE INTO fortune_cooldowns VALUES (?,?)", (user_id, ts))
    await db.commit()

async def is_banned(user_id):
    async with db.execute("SELECT reason, ban_end FROM banned_users WHERE user_id=?", (user_id,)) as c:
        row = await c.fetchone()
    if not row:
        return False, None, None
    end = datetime.fromisoformat(row[1])
    if datetime.now() < end:
        return True, row[0], row[1]
    else:
        await db.execute("DELETE FROM banned_users WHERE user_id=?", (user_id,))
        await db.commit()
        return False, None, None

async def get_boost(type):
    async with db.execute("SELECT multiplier, expires FROM boosts WHERE type=?", (type,)) as c:
        row = await c.fetchone()
    if not row:
        return 1.0
    if datetime.fromisoformat(row[1]) < datetime.now():
        await db.execute("DELETE FROM boosts WHERE type=?", (type,))
        await db.commit()
        return 1.0
    return row[0]

async def get_top_players():
    async with db.execute("""
        SELECT u.game_id, u.username, u.balance, COALESCE(SUM(b.price),0) as total_assets
        FROM users u
        LEFT JOIN businesses b ON u.user_id = b.user_id
        WHERE u.game_id IS NOT NULL
        GROUP BY u.user_id
        ORDER BY (u.balance + total_assets) DESC
        LIMIT 10
    """) as c:
        return await c.fetchall()

async def get_admin_stats():
    async with db.execute("SELECT COUNT(*), COALESCE(SUM(balance),0) FROM users") as c:
        row = await c.fetchone()
        return row[0], row[1]

async def reset_user(game_id):
    tg = await get_user_by_game_id(game_id)
    if not tg: return False
    await db.execute("UPDATE users SET balance=1000, last_work=NULL, last_bonus=NULL, last_collect=NULL WHERE user_id=?", (tg,))
    await db.execute("DELETE FROM businesses WHERE user_id=?", (tg,))
    await db.execute("DELETE FROM work_cooldowns WHERE user_id=?", (tg,))
    await db.execute("DELETE FROM casino_cooldowns WHERE user_id=?", (tg,))
    await db.execute("DELETE FROM fortune_cooldowns WHERE user_id=?", (tg,))
    await db.commit()
    return True

async def get_all_user_ids():
    async with db.execute("SELECT user_id FROM users") as c:
        return [row[0] for row in await c.fetchall()]

# ------------------------------------------------------------
# БОТ И ДИСПЕТЧЕР
# ------------------------------------------------------------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# ------------------------------------------------------------
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ------------------------------------------------------------
async def check_cooldown(last_iso, minutes=0, hours=0):
    if not last_iso:
        return True, ""
    last = datetime.fromisoformat(last_iso)
    delta = timedelta(minutes=minutes, hours=hours)
    now = datetime.now()
    if now < last + delta:
        rem = (last + delta) - now
        total_sec = int(rem.total_seconds())
        h, m = divmod(total_sec, 3600) if hours else (0,0)
        m, s = divmod(total_sec - h*3600, 60) if minutes else (0, total_sec)
        if h > 0:
            return False, f"{h}ч {m}мин"
        elif m > 0:
            return False, f"{m}мин {s}сек"
        else:
            return False, f"{s}сек"
    return True, ""

def parse_duration(s):
    match = re.match(r"(\d+)([mhdwMy]?)", s)
    if not match:
        raise ValueError("Неверный формат")
    val = int(match.group(1))
    unit = match.group(2) or 'm'
    if unit == 'm': return timedelta(minutes=val)
    elif unit == 'h': return timedelta(hours=val)
    elif unit == 'd': return timedelta(days=val)
    elif unit == 'w': return timedelta(weeks=val)
    elif unit == 'M': return timedelta(days=val*30)
    elif unit == 'y': return timedelta(days=val*365)
    else: raise ValueError("Неизвестная единица времени")

async def check_banned(user_id, message):
    banned, reason, end = await is_banned(user_id)
    if banned:
        await message.answer(f"🚫 Вы заблокированы до {end}\nПричина: {html_mod.escape(reason)}")
        return True
    return False

# ------------------------------------------------------------
# СТАРТ И ПРОФИЛЬ
# ------------------------------------------------------------
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, command: CommandObject = None):
    await state.clear()
    user_id = message.from_user.id
    user = await get_user(user_id)
    if user:
        if await check_banned(user_id, message): return
        await show_profile_content(message, user)
        return
    referred_by = None
    if command and command.args:
        arg = command.args.strip()
        if arg.startswith("ref"):
            try:
                ref_game_id = int(arg[3:])
                referred_by = ref_game_id
            except ValueError:
                pass
    await message.answer("👋 Привет! Добро пожаловать в симулятор жизни «Бомш».\nКак тебя зовут?")
    await state.set_state(Registration.waiting_for_name)
    await state.update_data(referred_by=referred_by)

@router.message(StateFilter(Registration.waiting_for_name))
async def process_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if not name or len(name) > 64:
        await message.answer("Имя должно быть от 1 до 64 символов.")
        return
    data = await state.get_data()
    referred_by = data.get("referred_by")
    await register_user(message.from_user.id, name, referred_by)
    await state.clear()
    user = await get_user(message.from_user.id)
    await message.answer(
        f"✅ Регистрация прошла успешно!\n"
        f"Твой игровой ID: <b>{user[5]}</b>\n"
        f"Баланс: <b>1000$</b>\n"
        f"Реферальный код: <code>{user[6]}</code>",
        reply_markup=main_kb, parse_mode="HTML"
    )

async def show_profile_content(message: Message, user: tuple):
    businesses = await get_businesses(user[0])
    total_inc = 0
    total_value = 0
    for b in businesses:
        total_value += b[4]
        supply_until = b[6]
        if supply_until and datetime.fromisoformat(supply_until) < datetime.now():
            continue
        boost = await get_boost("business")
        level = b[5]
        income = b[3]
        total_inc += int(income * level * boost)
    async with db.execute("SELECT COUNT(*) FROM work_cooldowns WHERE user_id=?", (user[0],)) as c:
        work_cnt = (await c.fetchone())[0]
    safe_username = html_mod.escape(user[1] or "Неизвестный")
    total_capital = user[2] + total_value
    text = (
        f"👤 <b>Профиль</b>\n"
        f"🆔 ID: {user[5]}\n"
        f"🎭 Имя: {safe_username}\n"
        f"💰 Баланс: <b>{user[2]:,}$</b>\n"
        f"🏢 Бизнесов: {len(businesses)}\n"
        f"📈 Доход/час: {total_inc}$\n"
        f"🏦 Общий капитал: <b>{total_capital:,}$</b>\n"
        f"🛠 Работ выполнено: {work_cnt}"
    )
    await message.answer(text, reply_markup=main_kb, parse_mode="HTML")

@router.message(F.text == "👤 Профиль")
async def profile(message: Message):
    if await check_banned(message.from_user.id, message): return
    user = await get_user(message.from_user.id)
    if not user:
        await message.answer("❌ Нажми /start для регистрации.")
        return
    await show_profile_content(message, user)

# ------------------------------------------------------------
# 💼 РАБОТА (исправленный показ правильного эмодзи)
# ------------------------------------------------------------
WORK_EMOJIS = {
    "taxi":       ["🚖","🚕","🚗","🚙"],
    "electric":   ["🔴","🟡","🔵","⚫"],
    "freelance":  ["🧑‍💻","👨‍💼","🕵️","🎅"],
    "builder":    ["🔨","🔧","🪚","🧹"],
    "cook":       ["🍎","🍔","🍕","🍣"],
    "programmer": ["0","1","{","}"],
    "police":     ["🎯","💣","🔫","🛡"],
    "pilot":      ["🛫","🛬","✈️","🛩"],
}

@router.message(F.text == "💼 Работа")
async def choose_work(message: Message):
    if await check_banned(message.from_user.id, message): return
    buttons = []
    for key, w in WORK_TYPES.items():
        buttons.append([InlineKeyboardButton(
            text=f"{w['name']} ({w['rounds']} раунда) — {w['income']}$",
            callback_data=f"work:{key}"
        )])
    await message.answer("🔨 Выберите работу:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.callback_query(F.data.startswith("work:"))
async def start_work_game(call: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = call.from_user.id
    work_key = call.data.split(":")[1]
    work = WORK_TYPES.get(work_key)
    if not work:
        await call.answer("❌ Неизвестная работа"); return

    can, info = await check_cooldown(await get_work_cooldown(user_id, work_key), minutes=work["cd_min"])
    if not can:
        await call.answer(f"⏳ Кулдаун! Осталось {info}", show_alert=True)
        return

    await state.set_state(WorkSession.waiting_for_answer)
    await state.update_data(
        work_key=work_key,
        round_num=1,
        total_rounds=work["rounds"],
        completed=0
    )
    await send_work_round(call.message, state)

async def send_work_round(message: Message, state: FSMContext):
    data = await state.get_data()
    work_key = data["work_key"]
    work = WORK_TYPES[work_key]
    round_num = data["round_num"]
    emojis = WORK_EMOJIS[work_key]
    correct = random.choice(emojis)
    shuffled = emojis[:]
    random.shuffle(shuffled)
    buttons = []
    for e in shuffled:
        buttons.append([InlineKeyboardButton(
            text=e,
            callback_data=f"workgame:{work_key}:{e}:{correct}:{round_num}"
        )])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await state.update_data(correct_this_round=correct)
    try:
        await message.edit_text(
            f"<b>{work['name']}</b> — Раунд {round_num}/{data['total_rounds']}\n"
            f"Найди {correct}",
            reply_markup=kb, parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка редактирования работы: {e}")

@router.callback_query(F.data.startswith("workgame:"), StateFilter(WorkSession.waiting_for_answer))
async def work_round_answer(call: CallbackQuery, state: FSMContext):
    try:
        parts = call.data.split(":")
        if len(parts) != 5:
            await call.answer("Некорректные данные", show_alert=True)
            return
        work_key, answer, correct, s_round = parts[1], parts[2], parts[3], parts[4]
    except ValueError:
        await call.answer("Некорректные данные", show_alert=True)
        return
    data = await state.get_data()
    if str(data["round_num"]) != s_round:
        await call.answer("❌ Не ваш раунд", show_alert=True)
        return

    if answer == correct:
        completed = data["completed"] + 1
        round_num = data["round_num"] + 1
        if round_num > data["total_rounds"]:
            work = WORK_TYPES[work_key]
            income = int(work["income"] * (await get_boost("work")))
            await update_balance(call.from_user.id, income)
            await set_work_cooldown(call.from_user.id, work_key, datetime.now().isoformat())
            user = await get_user(call.from_user.id)
            await call.message.edit_text(
                f"✅ Работа <b>{work['name']}</b> выполнена! +{income}$\n"
                f"💰 Баланс: <b>{user[2]:,}$</b>",
                parse_mode="HTML"
            )
            await state.clear()
        else:
            await state.update_data(completed=completed, round_num=round_num)
            await send_work_round(call.message, state)
    else:
        work = WORK_TYPES[work_key]
        total_rounds = data["total_rounds"]
        completed = data["completed"]
        partial_pay = int(work["income"] * 0.3 * completed)
        boost = await get_boost("work")
        partial_pay = int(partial_pay * boost)
        if partial_pay > 0:
            await update_balance(call.from_user.id, partial_pay)
        await set_work_cooldown(call.from_user.id, work_key, datetime.now().isoformat())
        user = await get_user(call.from_user.id)
        msg = f"😞 Ошибка в раунде! Работа закончена.\nПройдено {completed}/{total_rounds} раундов."
        if partial_pay > 0:
            msg += f"\nВы заработали <b>{partial_pay}$</b>."
        msg += f"\n💰 Баланс: <b>{user[2]:,}$</b>"
        await call.message.edit_text(msg, parse_mode="HTML")
        await state.clear()
    await call.answer()

# ------------------------------------------------------------
# 🏢 БИЗНЕСЫ (включая управление казино)
# ------------------------------------------------------------
@router.message(F.text == "🏢 Бизнесы")
async def biz_menu(message: Message):
    if await check_banned(message.from_user.id, message): return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏪 Купить бизнес", callback_data="buy_biz")],
        [InlineKeyboardButton(text="📦 Мои бизнесы", callback_data="my_biz")],
        [InlineKeyboardButton(text="⚙️ Управление", callback_data="manage_biz")],
        [InlineKeyboardButton(text="💰 Собрать доход", callback_data="collect_income")],
    ])
    await message.answer("🏢 Бизнесы:", reply_markup=kb)

@router.callback_query(F.data == "buy_biz")
async def list_biz(call: CallbackQuery):
    buttons = []
    for key, b in BUSINESS_TYPES.items():
        async with db.execute("SELECT COALESCE(max_count,3) FROM biz_limits WHERE biz_type=?", (key,)) as c:
            max_cnt = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM businesses WHERE biz_type=?", (key,)) as c:
            total_cnt = (await c.fetchone())[0]
        available = max_cnt - total_cnt
        if available <= 0:
            buttons.append([InlineKeyboardButton(text=f"{b['name']} (раскуплен)", callback_data="noop")])
        else:
            buttons.append([InlineKeyboardButton(
                text=f"{b['name']} — {b['price']:,}$ (доход {b['income']}$/ч) [{available}]",
                callback_data=f"buy:{key}:{b['price']}"
            )])
    await call.message.edit_text("🛒 Выберите бизнес:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()

@router.callback_query(F.data == "noop")
async def noop_handler(call: CallbackQuery):
    await call.answer("❌ Недоступно", show_alert=True)

@router.callback_query(F.data.startswith("buy:"))
async def purchase_biz(call: CallbackQuery):
    try:
        _, biz_type, price_str = call.data.split(":")
        price = int(price_str)
    except:
        await call.answer("❌ Ошибка данных", show_alert=True); return
    biz = BUSINESS_TYPES.get(biz_type)
    if not biz:
        await call.answer("❌ Неизвестный бизнес", show_alert=True); return
    async with db.execute("SELECT COALESCE(max_count,3) FROM biz_limits WHERE biz_type=?", (biz_type,)) as c:
        max_cnt = (await c.fetchone())[0]
    async with db.execute("SELECT COUNT(*) FROM businesses WHERE biz_type=?", (biz_type,)) as c:
        total_cnt = (await c.fetchone())[0]
    if total_cnt >= max_cnt:
        await call.answer("❌ Все бизнесы куплены", show_alert=True); return
    user = await get_user(call.from_user.id)
    if user[2] < price:
        await call.answer("💸 Недостаточно средств", show_alert=True); return
    await update_balance(call.from_user.id, -price)
    await add_business(call.from_user.id, biz_type, biz["name"], biz["income"], price)
    await call.message.edit_text(
        f"🎉 Вы купили <b>{biz['name']}</b> за {price:,}$\nДоход: {biz['income']}$/ч",
        parse_mode="HTML"
    )
    await call.answer()

@router.callback_query(F.data == "my_biz")
async def my_biz_list(call: CallbackQuery):
    user_id = call.from_user.id
    bizs = await get_businesses(user_id)
    if not bizs:
        await call.message.edit_text("📭 Нет бизнесов."); await call.answer(); return
    now = datetime.now()
    text = "📦 <b>Ваши бизнесы:</b>\n\n"
    for b in bizs:
        biz_id, biz_type, name, base_inc, price, lvl, supply_until, _ = b
        supply_ok = True
        supply_time_left = None
        if supply_until:
            supply_dt = datetime.fromisoformat(supply_until)
            if supply_dt < now:
                supply_ok = False
            else:
                delta = supply_dt - now
                hours, rem = divmod(delta.seconds, 3600)
                minutes = rem // 60
                supply_time_left = f"{hours}ч {minutes}мин" if hours > 0 else f"{minutes}мин"
        boost = await get_boost("business")
        if biz_type == "casino_pro":
            current_inc = 0
        else:
            current_inc = int(base_inc * lvl * boost)
        supply_status = "✅" if supply_ok else "⛔"
        time_str = f" (ещё {supply_time_left})" if supply_ok and supply_time_left else ""
        text += f"{supply_status} {name} (ур.{lvl}) — {current_inc}$/ч{time_str}\n"
    await call.message.edit_text(text, parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "manage_biz")
async def manage_biz_choose(call: CallbackQuery):
    user_id = call.from_user.id
    bizs = await get_businesses(user_id)
    if not bizs:
        await call.message.edit_text("📭 Нет бизнесов для управления."); await call.answer(); return
    buttons = []
    for b in bizs:
        biz_id, biz_type, name, inc, price, lvl, _, _ = b
        if biz_type == "casino_pro":
            text_btn = f"{name} (ур.{lvl}) — управление"
        else:
            cost = price * lvl
            text_btn = f"{name} (ур.{lvl}) — улучшить за {cost}$"
        buttons.append([InlineKeyboardButton(text=text_btn, callback_data=f"bizmanage:{biz_id}")])
    await call.message.edit_text("⚙️ Выберите бизнес для управления:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()

@router.callback_query(F.data.startswith("bizmanage:"))
async def biz_manage_options(call: CallbackQuery):
    biz_id = int(call.data.split(":")[1])
    async with db.execute(
        "SELECT id, user_id, biz_type, name, income_per_hour, price, level, supply_until FROM businesses WHERE id=?",
        (biz_id,)
    ) as c:
        biz = await c.fetchone()
    if not biz or biz[1] != call.from_user.id:
        await call.answer("❌ Нет доступа", show_alert=True); return
    biz_id, user_id, biz_type, name, income, price, level, supply_until = biz
    supply_info = ""
    if supply_until:
        dt = datetime.fromisoformat(supply_until)
        if dt > datetime.now():
            delta = dt - datetime.now()
            hours, rem = divmod(delta.seconds, 3600)
            minutes = rem // 60
            supply_info = f"🕒 Сырьё ещё {hours}ч {minutes}мин\n"
        else:
            supply_info = "⛔ Сырьё закончилось\n"
    kb = []
    if biz_type != "casino_pro":
        cost = price * level
        if level < 10:
            kb.append([InlineKeyboardButton(text=f"📈 Улучшить за {cost}$", callback_data=f"upgrade:{biz_id}")])
        kb.append([InlineKeyboardButton(text="📦 Заказать сырьё (24ч)", callback_data=f"supply:{biz_id}")])
    else:
        kb.append([InlineKeyboardButton(text="🎛 Настройки казино", callback_data=f"casino_settings:{biz_id}")])
        kb.append([InlineKeyboardButton(text="💰 Управление хранилищем", callback_data=f"casino_vault:{biz_id}")])
    kb.append([InlineKeyboardButton(text="💸 Выставить на аукцион", callback_data=f"sell_auction:{biz_id}")])
    await call.message.edit_text(
        f"⚙️ Управление <b>{html_mod.escape(name)}</b> (ур.{level})\n{supply_info}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML"
    )
    await call.answer()

@router.callback_query(F.data.startswith("upgrade:"))
async def upgrade_biz(call: CallbackQuery):
    biz_id = int(call.data.split(":")[1])
    async with db.execute("SELECT id, user_id, biz_type, price, level FROM businesses WHERE id=?", (biz_id,)) as c:
        biz = await c.fetchone()
    if not biz or biz[1] != call.from_user.id:
        await call.answer("❌", show_alert=True); return
    if biz[2] == "casino_pro":
        await call.answer("❌ Казино нельзя улучшить", show_alert=True); return
    price = int(biz[3]); level = int(biz[4])
    if level >= 10:
        await call.answer("Максимальный уровень достигнут", show_alert=True); return
    cost = price * level
    user = await get_user(call.from_user.id)
    if user[2] < cost:
        await call.answer(f"💸 Нужно {cost}$", show_alert=True); return
    await update_balance(call.from_user.id, -cost)
    await db.execute("UPDATE businesses SET level=level+1, price=price+? WHERE id=?", (price, biz_id))
    await db.commit()
    await call.message.edit_text(f"✅ Бизнес улучшен до уровня {level+1}")
    await call.answer()

@router.callback_query(F.data.startswith("supply:"))
async def supply_biz(call: CallbackQuery):
    biz_id = int(call.data.split(":")[1])
    async with db.execute("SELECT id, user_id FROM businesses WHERE id=?", (biz_id,)) as c:
        biz = await c.fetchone()
    if not biz or biz[1] != call.from_user.id:
        await call.answer("❌", show_alert=True); return
    until = (datetime.now() + timedelta(hours=24)).isoformat()
    await db.execute("UPDATE businesses SET supply_until=? WHERE id=?", (until, biz_id))
    await db.commit()
    await call.message.edit_text("📦 Сырьё заказано! Бизнес будет работать 24ч.")
    await call.answer()

# --- Управление казино ---
@router.callback_query(F.data.startswith("casino_settings:"))
async def casino_settings_menu(call: CallbackQuery):
    biz_id = int(call.data.split(":")[1])
    async with db.execute("SELECT id, user_id FROM businesses WHERE id=? AND biz_type='casino_pro'", (biz_id,)) as c:
        biz = await c.fetchone()
    if not biz or biz[1] != call.from_user.id:
        await call.answer("❌ Нет доступа", show_alert=True); return
    async with db.execute("SELECT * FROM casino_settings WHERE id=1") as c:
        sett = await c.fetchone()
    if not sett:
        await call.answer("Настройки не найдены"); return
    text = (
        "🎛 <b>Настройки казино</b>\n"
        f"• Мин. ставка (кости): {sett[1]}$\n"
        f"• Мин. ставка (блэкджек): {sett[3]}$\n"
        f"• Мин. ставка (фортуна): {sett[4]}$\n"
        f"• Шанс джекпота в костях: {sett[5]*100}%\n"
        f"• Множитель блэкджека: {sett[7]}\n"
        f"• Хранилище: {sett[8]}$\n"
        "\nИзменить: /set_casino &lt;параметр&gt; &lt;значение&gt;"
    )
    await call.message.edit_text(text, parse_mode="HTML")
    await call.answer()

@router.message(Command("set_casino"))
async def set_casino_param(message: Message, command: CommandObject):
    user_id = message.from_user.id
    async with db.execute("SELECT id FROM businesses WHERE user_id=? AND biz_type='casino_pro'", (user_id,)) as c:
        owner = await c.fetchone()
    if not owner and user_id != ADMIN_ID:
        await message.answer("❌ Только владелец казино может менять настройки.")
        return
    args = command.args.split() if command.args else []
    if len(args) < 2:
        await message.answer("Использование: /set_casino &lt;параметр&gt; &lt;значение&gt;\nПараметры: min_dice, min_bj, min_fortune, dice_chance, bj_mult")
        return
    param = args[0].lower()
    try:
        value = float(args[1]) if "chance" in param or "mult" in param else int(args[1])
    except ValueError:
        await message.answer("❌ Значение должно быть числом.")
        return
    column = None
    if param == "min_dice": column = "min_bet_dice"
    elif param == "min_bj": column = "min_bet_blackjack"
    elif param == "min_fortune": column = "min_bet_fortune"
    elif param == "dice_chance":
        if not 0 < value <= 1:
            await message.answer("Шанс от 0 до 1.")
            return
        column = "dice_win_chance"
    elif param == "bj_mult":
        if value <= 0:
            await message.answer("Множитель > 0")
            return
        column = "blackjack_win_multiplier"
    else:
        await message.answer("Неизвестный параметр.")
        return
    await db.execute(f"UPDATE casino_settings SET {column}=? WHERE id=1", (value,))
    await db.commit()
    await message.answer(f"✅ Параметр {param} обновлён.")

@router.callback_query(F.data.startswith("casino_vault:"))
async def casino_vault_menu(call: CallbackQuery):
    biz_id = int(call.data.split(":")[1])
    async with db.execute("SELECT id, user_id FROM businesses WHERE id=? AND biz_type='casino_pro'", (biz_id,)) as c:
        biz = await c.fetchone()
    if not biz or biz[1] != call.from_user.id:
        await call.answer("❌ Нет доступа", show_alert=True); return
    async with db.execute("SELECT vault FROM casino_settings WHERE id=1") as c:
        vault = (await c.fetchone())[0]
    await call.message.edit_text(f"💰 Хранилище казино: <b>{vault:,}$</b>\n\n"
                                 "Команды:\n"
                                 "/deposit_vault &lt;сумма&gt; – пополнить\n"
                                 "/withdraw_vault &lt;сумма&gt; – снять",
                                 parse_mode="HTML")
    await call.answer()

@router.message(Command("deposit_vault"))
async def deposit_vault(message: Message, command: CommandObject):
    user_id = message.from_user.id
    async with db.execute("SELECT id FROM businesses WHERE user_id=? AND biz_type='casino_pro'", (user_id,)) as c:
        if not await c.fetchone():
            await message.answer("❌ Только владелец казино может пополнять хранилище."); return
    if not command.args: await message.answer("Введите сумму: /deposit_vault &lt;сумма&gt;"); return
    try: amount = int(command.args.strip())
    except: await message.answer("Число."); return
    user = await get_user(user_id)
    if user[2] < amount:
        await message.answer("💸 Недостаточно средств."); return
    await update_balance(user_id, -amount)
    await db.execute("UPDATE casino_settings SET vault=vault+? WHERE id=1", (amount,))
    await db.commit()
    await message.answer(f"✅ Хранилище пополнено на {amount}$.")

@router.message(Command("withdraw_vault"))
async def withdraw_vault(message: Message, command: CommandObject):
    user_id = message.from_user.id
    async with db.execute("SELECT id FROM businesses WHERE user_id=? AND biz_type='casino_pro'", (user_id,)) as c:
        if not await c.fetchone():
            await message.answer("❌ Только владелец казино."); return
    if not command.args: await message.answer("/withdraw_vault &lt;сумма&gt;"); return
    try: amount = int(command.args.strip())
    except: await message.answer("Число."); return
    async with db.execute("SELECT vault FROM casino_settings WHERE id=1") as c:
        vault = (await c.fetchone())[0]
    if amount > vault:
        await message.answer("❌ В хранилище недостаточно денег."); return
    await update_balance(user_id, amount)
    await db.execute("UPDATE casino_settings SET vault=vault-? WHERE id=1", (amount,))
    await db.commit()
    await message.answer(f"✅ Вы сняли {amount}$ из хранилища.")

# --- Сбор дохода ---
@router.callback_query(F.data == "collect_income")
async def collect_income(call: CallbackQuery):
    user_id = call.from_user.id
    user = await get_user(user_id)
    if not user:
        await call.answer("❌ /start", show_alert=True); return
    can, info = await check_cooldown(user[4], hours=COLLECT_CD_HOURS)
    if not can:
        await call.answer(f"⏳ Сбор раз в {COLLECT_CD_HOURS}ч. Осталось {info}", show_alert=True); return
    bizs = await get_businesses(user_id)
    if not bizs:
        await call.message.edit_text("📭 Нет бизнесов."); await call.answer(); return
    ref_time = datetime.fromisoformat(user[4]) if user[4] else datetime(1970,1,1)
    now = datetime.now()
    total = 0
    for b in bizs:
        biz_id, biz_type, name, base_inc, price, lvl, supply_until, purchase_date = b
        if biz_type == "casino_pro":
            continue
        if supply_until and datetime.fromisoformat(supply_until) > now:
            current_inc = int(base_inc * lvl * (await get_boost("business")))
            start = max(ref_time, datetime.fromisoformat(purchase_date))
            if start < now:
                total += int(((now - start).total_seconds() / 3600) * current_inc)
    if total <= 0:
        await call.answer("Доход пока не накопился.", show_alert=True); return
    await update_balance(user_id, total)
    await set_last_collect(user_id, now.isoformat())
    await call.message.edit_text(f"💰 Собрано: <b>{total:,}$</b>", parse_mode="HTML")
    await call.answer()

# ------------------------------------------------------------
# 🎁 БОНУС
# ------------------------------------------------------------
@router.message(F.text == "🎁 Бонус")
async def daily_bonus(message: Message):
    if await check_banned(message.from_user.id, message): return
    user = await get_user(message.from_user.id)
    if not user: await message.answer("❌ /start"); return
    can, info = await check_cooldown(user[3], hours=24)
    if not can:
        await message.answer(f"⏳ Бонус доступен раз в 24ч. Осталось {info}"); return
    bonus = random.randint(1000, 5000)
    await update_balance(message.from_user.id, bonus)
    await set_last_bonus(message.from_user.id, datetime.now().isoformat())
    user = await get_user(message.from_user.id)
    await message.answer(f"🎁 Бонус: <b>{bonus:,}$</b>\n💰 Баланс: <b>{user[2]:,}$</b>", parse_mode="HTML")

# ------------------------------------------------------------
# 🎰 КАЗИНО (без рулетки)
# ------------------------------------------------------------
@router.message(F.text == "🎰 Казино")
async def casino_main(message: Message, state: FSMContext):
    if await check_banned(message.from_user.id, message): return
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Кости (x2/x5)", callback_data="casino_dice")],
        [InlineKeyboardButton(text="♠️ Блэкджек", callback_data="casino_bj")],
        [InlineKeyboardButton(text="🎡 Колесо фортуны (24ч кд)", callback_data="casino_fortune")],
        [InlineKeyboardButton(text="⚔️ Дуэль (против игрока)", callback_data="casino_duel")],
    ])
    await message.answer("🎰 Выберите режим казино:", reply_markup=kb)

# Кости
@router.callback_query(F.data == "casino_dice")
async def dice_bet(call: CallbackQuery):
    user_id = call.from_user.id
    can, info = await check_cooldown(await get_casino_cd(user_id), minutes=CASINO_CD_MINUTES)
    if not can:
        await call.answer(f"⏳ Кулдаун {info}", show_alert=True); return
    async with db.execute("SELECT min_bet_dice FROM casino_settings WHERE id=1") as c:
        min_bet = (await c.fetchone())[0]
    bets = [bet for bet in [100, 500, 1000, 5000, 10000] if bet >= min_bet]
    if not bets:
        await call.answer("🚫 Ставки временно недоступны", show_alert=True); return
    buttons = [[InlineKeyboardButton(text=f"{b}$", callback_data=f"dice_play:{b}")] for b in bets]
    await call.message.edit_text("🎲 Выберите ставку:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()

@router.callback_query(F.data.startswith("dice_play:"))
async def dice_play(call: CallbackQuery):
    user_id = call.from_user.id
    bet = int(call.data.split(":")[1])
    user = await get_user(user_id)
    if user[2] < bet:
        await call.answer("💸 Недостаточно средств", show_alert=True); return
    await update_balance(user_id, -bet)
    boost = await get_boost("casino")
    async with db.execute("SELECT dice_win_chance FROM casino_settings WHERE id=1") as c:
        win_chance = (await c.fetchone())[0]
    r = random.random()
    if r < win_chance:
        win = int(bet * 5 * boost) if boost != 1 else bet * 5
        msg = f"🎉 ДЖЕКПОТ! Выигрыш: <b>{win}$</b> (x5)"
    elif r < 0.5:
        win = int(bet * 2 * boost) if boost != 1 else bet * 2
        msg = f"🎊 Выигрыш: <b>{win}$</b> (x2)"
    else:
        win = 0
        msg = f"😞 Проигрыш: <b>{bet}$</b>"
    if win:
        async with db.execute("SELECT vault FROM casino_settings WHERE id=1") as c:
            vault = (await c.fetchone())[0]
        if vault < win:
            msg += "\n⚠️ В казино недостаточно средств, выигрыш не выплачен."
        else:
            await db.execute("UPDATE casino_settings SET vault=vault-? WHERE id=1", (win,))
            await update_balance(user_id, win)
    await set_casino_cd(user_id, datetime.now().isoformat())
    new_bal = (await get_user(user_id))[2]
    await call.message.edit_text(f"{msg}\n💰 Баланс: <b>{new_bal:,}$</b>", parse_mode="HTML")
    await call.answer()

# Блэкджек
CARD_VALUES = {"2":2,"3":3,"4":4,"5":5,"6":6,"7":7,"8":8,"9":9,"10":10,"J":10,"Q":10,"K":10,"A":11}
CARD_NAMES = ["2","3","4","5","6","7","8","9","10","J","Q","K","A"]

def draw_card(): return random.choice(CARD_NAMES)
def hand_value(cards):
    val = sum(CARD_VALUES[c] for c in cards)
    aces = cards.count("A")
    while val > 21 and aces > 0:
        val -= 10
        aces -= 1
    return val

@router.callback_query(F.data == "casino_bj")
async def bj_start(call: CallbackQuery, state: FSMContext):
    async with db.execute("SELECT min_bet_blackjack FROM casino_settings WHERE id=1") as c:
        min_bet = (await c.fetchone())[0]
    user = await get_user(call.from_user.id)
    if user[2] < min_bet:
        await call.answer(f"💸 Мин. ставка {min_bet}$", show_alert=True); return
    await state.update_data(bj_state="bet")
    await call.message.edit_text(f"♠️ Введите ставку (мин. {min_bet}$):")
    await state.set_state(Blackjack.playing)
    await call.answer()

@router.message(StateFilter(Blackjack.playing))
async def bj_bet(message: Message, state: FSMContext):
    data = await state.get_data()
    if data.get("bj_state") != "bet": return
    try: bet = int(message.text.strip())
    except: await message.answer("Введите число."); return
    async with db.execute("SELECT min_bet_blackjack FROM casino_settings WHERE id=1") as c:
        min_bet = (await c.fetchone())[0]
    if bet < min_bet: await message.answer(f"Мин. {min_bet}$"); return
    user = await get_user(message.from_user.id)
    if user[2] < bet: await message.answer("💸 Недостаточно средств"); return
    player = [draw_card(), draw_card()]
    dealer = [draw_card(), draw_card()]
    await state.update_data(bj_state="playing", bet=bet, player=player, dealer=dealer)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Взять", callback_data="bj_hit"),
         InlineKeyboardButton(text="Хватит", callback_data="bj_stand")],
        [InlineKeyboardButton(text="Удвоить", callback_data="bj_double")]
    ])
    await message.answer(f"♠️ Ваши карты: {', '.join(player)} ({hand_value(player)})\nДилер: {dealer[0]} ?", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("bj_"), StateFilter(Blackjack.playing))
async def bj_action(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if data.get("bj_state") != "playing": await call.answer("Не активна"); return
    action = call.data.split("_")[1]
    player = data["player"]
    dealer = data["dealer"]
    bet = data["bet"]
    user_id = call.from_user.id
    if action == "hit":
        player.append(draw_card())
        val = hand_value(player)
        if val > 21:
            await state.clear()
            await call.message.edit_text(f"💥 Перебор! Вы проиграли {bet}$")
            await update_balance(user_id, -bet)
            await call.answer(); return
        await state.update_data(player=player)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Взять", callback_data="bj_hit"),
             InlineKeyboardButton(text="Хватит", callback_data="bj_stand")],
            [InlineKeyboardButton(text="Удвоить", callback_data="bj_double")] if len(player)==2 else []
        ])
        await call.message.edit_text(f"Карты: {', '.join(player)} ({val})\nДилер: {dealer[0]} ?", reply_markup=kb)
        await call.answer()
    elif action == "stand":
        while hand_value(dealer) < 17: dealer.append(draw_card())
        pv, dv = hand_value(player), hand_value(dealer)
        await state.clear()
        if dv > 21 or pv > dv:
            async with db.execute("SELECT blackjack_win_multiplier FROM casino_settings WHERE id=1") as c:
                mult = (await c.fetchone())[0]
            win = int(bet * mult)
            await update_balance(user_id, win)
            msg = f"🎉 Победа! +{win}$"
        elif pv == dv:
            msg = "🤝 Ничья"
        else:
            await update_balance(user_id, -bet)
            msg = f"😞 Поражение -{bet}$"
        await call.message.edit_text(f"♠️ Итог: {msg}\nВаши: {pv}, Дилер: {dv}", parse_mode="HTML")
        await call.answer()
    elif action == "double":
        if len(player) != 2: await call.answer("Нельзя"); return
        user = await get_user(user_id)
        if user[2] < bet: await call.answer("💸"); return
        await update_balance(user_id, -bet)
        bet *= 2
        player.append(draw_card())
        pv = hand_value(player)
        if pv > 21:
            await state.clear()
            await call.message.edit_text(f"💥 Перебор! Проигрыш {bet}$")
            await call.answer(); return
        while hand_value(dealer) < 17: dealer.append(draw_card())
        dv = hand_value(dealer)
        await state.clear()
        if dv > 21 or pv > dv:
            async with db.execute("SELECT blackjack_win_multiplier FROM casino_settings WHERE id=1") as c:
                mult = (await c.fetchone())[0]
            win = int(bet * mult)
            msg = f"🎉 Победа! +{win}$"
        elif pv == dv: msg = "🤝 Ничья"
        else: msg = f"😞 Поражение -{bet}$"
        await call.message.edit_text(f"♠️ Итог удвоения: {msg}", parse_mode="HTML")
        await call.answer()

# Колесо фортуны
FORTUNE_PRIZES = [(0,0.25),(0.1,0.30),(0.5,0.20),(1,0.12),(2,0.07),(3,0.03),(5,0.02),(10,0.008),(100,0.002)]
_wsum = sum(w for _,w in FORTUNE_PRIZES)
FORTUNE_WHEEL = [(m, w/_wsum) for m,w in FORTUNE_PRIZES]

@router.callback_query(F.data == "casino_fortune")
async def fortune_start(call: CallbackQuery):
    user_id = call.from_user.id
    can, info = await check_cooldown(await get_fortune_cd(user_id), hours=FORTUNE_CD_HOURS)
    if not can:
        await call.answer(f"⏳ Фортуна раз в {FORTUNE_CD_HOURS}ч. Осталось {info}", show_alert=True); return
    async with db.execute("SELECT min_bet_fortune FROM casino_settings WHERE id=1") as c:
        min_bet = (await c.fetchone())[0]
    user = await get_user(user_id)
    if user[2] < min_bet:
        await call.answer(f"💸 Мин. ставка {min_bet}$", show_alert=True); return
    bets = [b for b in [1000, 5000, 10000, 50000] if b >= min_bet]
    if not bets:
        await call.answer("🚫 Ставки недоступны"); return
    buttons = [[InlineKeyboardButton(text=f"{b}$", callback_data=f"fortune_spin:{b}")] for b in bets]
    await call.message.edit_text("🎡 Выберите ставку:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()

@router.callback_query(F.data.startswith("fortune_spin:"))
async def fortune_spin(call: CallbackQuery):
    user_id = call.from_user.id
    bet = int(call.data.split(":")[1])
    user = await get_user(user_id)
    if user[2] < bet:
        await call.answer("💸 Недостаточно средств", show_alert=True); return
    await update_balance(user_id, -bet)
    r = random.random()
    cum = 0; mult = 0
    for m,prob in FORTUNE_WHEEL:
        cum += prob
        if r < cum: mult = m; break
    boost = await get_boost("casino")
    win = int(bet * mult * boost) if mult > 0 else 0
    if win:
        async with db.execute("SELECT vault FROM casino_settings WHERE id=1") as c:
            vault = (await c.fetchone())[0]
        if vault >= win:
            await db.execute("UPDATE casino_settings SET vault=vault-? WHERE id=1", (win,))
            await update_balance(user_id, win)
            if mult == 100: msg = f"🎉 ДЖЕКПОТ x100! {win}$"
            else: msg = f"🎡 x{mult}! Выигрыш {win}$"
        else:
            msg = f"😞 x{mult}, но в казино недостаточно денег."
    else:
        msg = f"😞 x0. Потеря {bet}$"
    await set_fortune_cd(user_id, datetime.now().isoformat())
    new_bal = (await get_user(user_id))[2]
    await call.message.edit_text(f"{msg}\n💰 Баланс: <b>{new_bal:,}$</b>", parse_mode="HTML")
    await call.answer()

# Дуэль
@router.callback_query(F.data == "casino_duel")
async def duel_start(call: CallbackQuery):
    user_id = call.from_user.id
    async with db.execute("SELECT bet FROM duel_queue WHERE user_id=?", (user_id,)) as c:
        if await c.fetchone():
            await call.answer("❌ Вы уже в очереди на дуэль", show_alert=True); return
    bets = [100, 500, 1000, 5000, 10000]
    buttons = [[InlineKeyboardButton(text=f"{b}$", callback_data=f"duel_bet:{b}")] for b in bets]
    await call.message.edit_text("⚔️ Выберите ставку для дуэли:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()

@router.callback_query(F.data.startswith("duel_bet:"))
async def duel_bet(call: CallbackQuery):
    user_id = call.from_user.id
    bet = int(call.data.split(":")[1])
    user = await get_user(user_id)
    if user[2] < bet:
        await call.answer("💸 Недостаточно средств", show_alert=True); return
    async with db.execute("SELECT user_id, bet, timestamp FROM duel_queue LIMIT 1") as c:
        opponent = await c.fetchone()
    if opponent:
        opp_id, opp_bet, opp_ts = opponent
        if opp_bet != bet:
            await call.answer("❌ Ставка не совпадает с ожидающим.", show_alert=True); return
        await db.execute("DELETE FROM duel_queue WHERE user_id=?", (opp_id,))
        await db.commit()
        await update_balance(user_id, -bet)
        await update_balance(opp_id, -bet)
        our_roll = random.randint(1, 6)
        opp_roll = random.randint(1, 6)
        result = f"🎲 Вы: {our_roll}, соперник: {opp_roll}\n"
        if our_roll > opp_roll:
            win = bet * 2
            await update_balance(user_id, win)
            result += f"🏆 Вы выиграли {bet}$ (x2)!"
        elif our_roll < opp_roll:
            await update_balance(opp_id, bet * 2)
            result += "😞 Вы проиграли."
        else:
            await update_balance(user_id, bet)
            await update_balance(opp_id, bet)
            result += "🤝 Ничья, ставки возвращены."
        try: await bot.send_message(user_id, f"⚔️ Дуэль завершена!\n{result}\n💰 Баланс: <b>{(await get_user(user_id))[2]:,}$</b>", parse_mode="HTML")
        except: pass
        try: await bot.send_message(opp_id, f"⚔️ Дуэль завершена!\n{result}\n💰 Баланс: <b>{(await get_user(opp_id))[2]:,}$</b>", parse_mode="HTML")
        except: pass
        await call.message.edit_text(f"✅ Дуэль начата! Бросок...\n{result}\n💰 Ваш баланс: <b>{(await get_user(user_id))[2]:,}$</b>", parse_mode="HTML")
        await call.answer()
    else:
        await db.execute("INSERT INTO duel_queue (user_id, bet, timestamp) VALUES (?,?,?)", (user_id, bet, datetime.now().isoformat()))
        await db.commit()
        await call.message.edit_text(f"⚔️ Вы в очереди на дуэль со ставкой {bet}$.\nОжидание {DUEL_TIMEOUT} секунд...")
        await call.answer()
        await asyncio.sleep(DUEL_TIMEOUT)
        async with db.execute("SELECT user_id FROM duel_queue WHERE user_id=?", (user_id,)) as c:
            if await c.fetchone():
                await db.execute("DELETE FROM duel_queue WHERE user_id=?", (user_id,))
                await db.commit()
                try: await bot.send_message(user_id, "⌛ Соперник не найден, дуэль отменена.")
                except: pass

# ------------------------------------------------------------
# 🤝 ВЗАИМОДЕЙСТВИЯ
# ------------------------------------------------------------
@router.message(F.text == "🤝 Взаимодействия")
async def interact_menu(message: Message, state: FSMContext):
    if await check_banned(message.from_user.id, message): return
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Перевести деньги", callback_data="transfer_money")],
    ])
    await message.answer("🤝 Выберите действие:", reply_markup=kb)

@router.callback_query(F.data == "transfer_money")
async def transfer_start(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("Введите <b>игровой ID</b> получателя:", parse_mode="HTML")
    await state.set_state(Transfer.waiting_for_id)
    await call.answer()

@router.message(StateFilter(Transfer.waiting_for_id, Transfer.waiting_for_amount), F.text.startswith("/"))
async def cancel_transfer(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Перевод отменён.")

@router.message(StateFilter(Transfer.waiting_for_id))
async def transfer_get_id(message: Message, state: FSMContext):
    try: target_gid = int(message.text.strip())
    except: await message.answer("❌ ID должен быть числом."); return
    target_tg = await get_user_by_game_id(target_gid)
    if not target_tg: await message.answer("❌ Игрок не найден."); return
    if target_tg == message.from_user.id:
        await message.answer("❌ Нельзя самому себе."); await state.clear(); return
    await state.update_data(target_tg=target_tg, target_gid=target_gid)
    await message.answer("Введите сумму перевода:")
    await state.set_state(Transfer.waiting_for_amount)

@router.message(StateFilter(Transfer.waiting_for_amount))
async def transfer_amount(message: Message, state: FSMContext):
    try: amount = int(message.text.strip())
    except: await message.answer("Число."); return
    if amount <= 0: await message.answer(">0"); return
    user = await get_user(message.from_user.id)
    if user[2] < amount: await message.answer(f"💸 Недостаточно. Баланс: {user[2]:,}$"); await state.clear(); return
    data = await state.get_data()
    await update_balance(message.from_user.id, -amount)
    await update_balance(data["target_tg"], amount)
    await state.clear()
    new_bal = (await get_user(message.from_user.id))[2]
    await message.answer(f"✅ Переведено <b>{amount}$</b> игроку ID <b>{data['target_gid']}</b>.\n💰 Ваш баланс: <b>{new_bal:,}$</b>", parse_mode="HTML")

# ------------------------------------------------------------
# 🏆 ТОП
# ------------------------------------------------------------
@router.message(F.text == "🏆 Топ игроков")
async def top_msg(message: Message):
    top = await get_top_players()
    if not top: await message.answer("📊 Топ пока пуст."); return
    text = "🏆 <b>Топ игроков</b>\n\n"
    for i,(gid,name,bal,ass) in enumerate(top,1):
        safe_name = html_mod.escape(name or f"ID {gid}")
        total = bal + ass
        text += f"{i}. {safe_name} (🆔{gid}) — {total:,}$\n"
    await message.answer(text, parse_mode="HTML")

# ------------------------------------------------------------
# 🛠 АДМИН-ПАНЕЛЬ
# ------------------------------------------------------------
@router.message(Command("admin"))
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID: return
    text = (
        "🛠 <b>Админ-панель</b>\n\n"
        "/admin_stats\n"
        "/give &lt;game_id&gt; &lt;сумма&gt;\n"
        "/take &lt;game_id&gt; &lt;сумма&gt;\n"
        "/setbalance &lt;game_id&gt; &lt;сумма&gt;\n"
        "/reset &lt;game_id&gt;\n"
        "/addbusiness &lt;game_id&gt; &lt;тип&gt;\n"
        "/removebusiness &lt;game_id&gt; &lt;тип&gt;\n"
        "/clearcd &lt;game_id&gt; &lt;работа&gt;\n"
        "/clearcollect &lt;game_id&gt;\n"
        "/clearcasinocd &lt;game_id&gt;\n"
        "/clearfortunecd &lt;game_id&gt;\n"
        "/clearallcd &lt;game_id&gt;\n"
        "/ban &lt;game_id&gt; &lt;длительность&gt; &lt;причина&gt;\n"
        "/unban &lt;game_id&gt;\n"
        "/boost &lt;тип&gt; &lt;множитель&gt; &lt;длительность&gt; (work/business/casino/all)\n"
        "/promo_create &lt;код&gt; &lt;сумма&gt; &lt;активаций&gt; &lt;длительность&gt;\n"
        "/promo_delete &lt;код&gt;\n"
        "/broadcast &lt;текст&gt;\n"
        "/listusers\n"
        "/addbalanceall &lt;сумма&gt;\n"
        "/setname &lt;game_id&gt; &lt;имя&gt;\n"
        "/setbizlimit &lt;тип&gt; &lt;макс. количество&gt;\n"
    )
    await message.answer(text, parse_mode="HTML")

@router.message(Command("admin_stats"))
async def admin_stats(message: Message):
    if message.from_user.id != ADMIN_ID: return
    p, b = await get_admin_stats()
    await message.answer(f"📊 Игроков: {p}\n💰 Суммарный баланс: {b:,}$")

async def resolve_target_id(arg: str) -> int | None:
    try: return await get_user_by_game_id(int(arg))
    except: return None

@router.message(Command("give"))
async def admin_give(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    args = command.args.split() if command.args else []
    if len(args) != 2: await message.answer("❌ /give &lt;game_id&gt; &lt;сумма&gt;", parse_mode="HTML"); return
    tg = await resolve_target_id(args[0])
    if not tg: await message.answer("❌ Игрок не найден."); return
    try: amount = int(args[1])
    except: await message.answer("❌ Сумма должна быть числом."); return
    await update_balance(tg, amount)
    await message.answer(f"✅ Игроку {args[0]} выдано {amount}$.")

@router.message(Command("take"))
async def admin_take(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    args = command.args.split() if command.args else []
    if len(args) != 2: await message.answer("❌ /take &lt;game_id&gt; &lt;сумма&gt;", parse_mode="HTML"); return
    tg = await resolve_target_id(args[0])
    if not tg: await message.answer("❌ Игрок не найден."); return
    try: amount = int(args[1])
    except: await message.answer("❌ Сумма должна быть числом."); return
    await update_balance(tg, -amount)
    await message.answer(f"✅ У игрока {args[0]} снято {amount}$.")

@router.message(Command("setbalance"))
async def admin_setbalance(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    args = command.args.split() if command.args else []
    if len(args) != 2: await message.answer("❌ /setbalance &lt;game_id&gt; &lt;сумма&gt;", parse_mode="HTML"); return
    tg = await resolve_target_id(args[0])
    if not tg: await message.answer("❌ Игрок не найден."); return
    try: new_bal = int(args[1])
    except: await message.answer("❌ Сумма должна быть числом."); return
    await db.execute("UPDATE users SET balance=? WHERE user_id=?", (new_bal, tg))
    await db.commit()
    await message.answer(f"✅ Баланс {args[0]} установлен на {new_bal}$.")

@router.message(Command("reset"))
async def admin_reset(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    if not command.args: await message.answer("❌ /reset &lt;game_id&gt;", parse_mode="HTML"); return
    if not await resolve_target_id(command.args.strip()): await message.answer("❌ Игрок не найден."); return
    await reset_user(int(command.args.strip()))
    await message.answer(f"✅ Игрок {command.args.strip()} сброшен.")

@router.message(Command("addbusiness"))
async def admin_addbusiness(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    args = command.args.split() if command.args else []
    if len(args) != 2: await message.answer("❌ /addbusiness &lt;game_id&gt; &lt;тип&gt;", parse_mode="HTML"); return
    tg = await resolve_target_id(args[0])
    if not tg: await message.answer("❌ Игрок не найден."); return
    biz = BUSINESS_TYPES.get(args[1].lower())
    if not biz: await message.answer("❌ Неверный тип."); return
    await add_business(tg, args[1].lower(), biz["name"], biz["income"], biz["price"])
    await message.answer(f"✅ Игроку {args[0]} добавлен бизнес «{biz['name']}».")

@router.message(Command("removebusiness"))
async def admin_removebusiness(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    args = command.args.split() if command.args else []
    if len(args) != 2: await message.answer("❌ /removebusiness &lt;game_id&gt; &lt;тип&gt;", parse_mode="HTML"); return
    tg = await resolve_target_id(args[0])
    if not tg: await message.answer("❌ Игрок не найден."); return
    biz_type = args[1].lower()
    cur = await db.execute("SELECT id FROM businesses WHERE user_id=? AND biz_type=? LIMIT 1", (tg, biz_type))
    row = await cur.fetchone()
    if not row: await message.answer(f"❌ У игрока нет бизнеса типа {biz_type}."); return
    await db.execute("DELETE FROM businesses WHERE id=?", (row[0],))
    await db.commit()
    await message.answer(f"✅ У игрока {args[0]} удалён один бизнес типа {biz_type}.")

@router.message(Command("clearcd"))
async def admin_clearcd(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    args = command.args.split() if command.args else []
    if len(args) != 2: await message.answer("❌ /clearcd &lt;game_id&gt; &lt;работа&gt;", parse_mode="HTML"); return
    tg = await resolve_target_id(args[0])
    if not tg: await message.answer("❌ Игрок не найден."); return
    work = args[1].lower()
    if work not in WORK_TYPES: await message.answer("❌ Неверная работа."); return
    await db.execute("DELETE FROM work_cooldowns WHERE user_id=? AND work_type=?", (tg, work))
    await db.commit()
    await message.answer(f"✅ Кулдаун работы {work} для {args[0]} сброшен.")

@router.message(Command("clearcollect"))
async def admin_clearcollect(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    if not command.args: await message.answer("❌ /clearcollect &lt;game_id&gt;", parse_mode="HTML"); return
    tg = await resolve_target_id(command.args.strip())
    if not tg: await message.answer("❌ Игрок не найден."); return
    await db.execute("UPDATE users SET last_collect=NULL WHERE user_id=?", (tg,))
    await db.commit()
    await message.answer(f"✅ Кулдаун сбора для {command.args.strip()} сброшен.")

@router.message(Command("clearcasinocd"))
async def admin_clearcasinocd(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    if not command.args: await message.answer("❌ /clearcasinocd &lt;game_id&gt;", parse_mode="HTML"); return
    tg = await resolve_target_id(command.args.strip())
    if not tg: await message.answer("❌ Игрок не найден."); return
    await db.execute("DELETE FROM casino_cooldowns WHERE user_id=?", (tg,))
    await db.commit()
    await message.answer(f"✅ Кулдаун казино (кости) для {command.args.strip()} сброшен.")

@router.message(Command("clearfortunecd"))
async def admin_clearfortunecd(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    if not command.args: await message.answer("❌ /clearfortunecd &lt;game_id&gt;", parse_mode="HTML"); return
    tg = await resolve_target_id(command.args.strip())
    if not tg: await message.answer("❌ Игрок не найден."); return
    await db.execute("DELETE FROM fortune_cooldowns WHERE user_id=?", (tg,))
    await db.commit()
    await message.answer(f"✅ Кулдаун фортуны для {command.args.strip()} сброшен.")

@router.message(Command("clearallcd"))
async def admin_clearallcd(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    if not command.args: await message.answer("❌ /clearallcd &lt;game_id&gt;", parse_mode="HTML"); return
    tg = await resolve_target_id(command.args.strip())
    if not tg: await message.answer("❌ Игрок не найден."); return
    await db.execute("DELETE FROM work_cooldowns WHERE user_id=?", (tg,))
    await db.execute("DELETE FROM casino_cooldowns WHERE user_id=?", (tg,))
    await db.execute("DELETE FROM fortune_cooldowns WHERE user_id=?", (tg,))
    await db.execute("UPDATE users SET last_bonus=NULL, last_collect=NULL WHERE user_id=?", (tg,))
    await db.commit()
    await message.answer(f"✅ Все КД игрока {command.args.strip()} сброшены")

@router.message(Command("ban"))
async def ban_user(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    args = command.args.split() if command.args else []
    if len(args) < 3:
        await message.answer("❌ /ban &lt;game_id&gt; &lt;длительность&gt; &lt;причина&gt;", parse_mode="HTML"); return
    gid, dur_str, reason = args[0], args[1], " ".join(args[2:])
    tg = await resolve_target_id(gid)
    if not tg: await message.answer("❌ Игрок не найден"); return
    try: delta = parse_duration(dur_str)
    except ValueError: await message.answer("❌ Неверный формат длительности (1m,1h,1d,1w,1M,1y)"); return
    until = datetime.now() + delta
    await db.execute("INSERT OR REPLACE INTO banned_users (user_id, reason, ban_end) VALUES (?,?,?)", (tg, reason, until.isoformat()))
    await db.commit()
    await message.answer(f"✅ Игрок {gid} забанен до {until.strftime('%Y-%m-%d %H:%M')}")

@router.message(Command("unban"))
async def unban_user(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    if not command.args: await message.answer("❌ /unban &lt;game_id&gt;", parse_mode="HTML"); return
    tg = await resolve_target_id(command.args.strip())
    if not tg: await message.answer("❌ Игрок не найден"); return
    await db.execute("DELETE FROM banned_users WHERE user_id=?", (tg,))
    await db.commit()
    await message.answer(f"✅ Игрок {command.args.strip()} разбанен")

@router.message(Command("boost"))
async def boost_game(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    args = command.args.split() if command.args else []
    if len(args) < 3:
        await message.answer("❌ /boost &lt;тип&gt; &lt;множитель&gt; &lt;длительность&gt;", parse_mode="HTML"); return
    type, mult_str, dur_str = args[0], args[1], args[2]
    try: mult = float(mult_str); delta = parse_duration(dur_str)
    except ValueError: await message.answer("❌ Ошибка в числе или длительности"); return
    until = datetime.now() + delta
    if type == "all":
        for t in ["work", "business", "casino"]:
            await db.execute("INSERT OR REPLACE INTO boosts (type, multiplier, expires) VALUES (?,?,?)", (t, mult, until.isoformat()))
    else:
        if type not in ["work", "business", "casino"]: await message.answer("❌ Неверный тип"); return
        await db.execute("INSERT OR REPLACE INTO boosts (type, multiplier, expires) VALUES (?,?,?)", (type, mult, until.isoformat()))
    await db.commit()
    await message.answer(f"✅ Буст x{mult} на {type} до {until.strftime('%Y-%m-%d %H:%M')}")

@router.message(Command("promo_create"))
async def promo_create(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    args = command.args.split() if command.args else []
    if len(args) < 4:
        await message.answer("❌ /promo_create &lt;код&gt; &lt;сумма&gt; &lt;активаций&gt; &lt;длительность&gt;", parse_mode="HTML"); return
    code, amount, activ, dur_str = args[0], int(args[1]), int(args[2]), args[3]
    try: delta = parse_duration(dur_str)
    except: await message.answer("❌ Неверная длительность"); return
    expires = datetime.now() + delta
    await db.execute("INSERT OR REPLACE INTO promo_codes (code, amount, activations_left, expires) VALUES (?,?,?,?)",
                     (code, amount, activ, expires.isoformat()))
    await db.commit()
    await message.answer(f"✅ Промокод {code} создан: {amount}$ x{activ} до {expires}")

@router.message(Command("promo_delete"))
async def promo_delete(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    if not command.args: await message.answer("❌ /promo_delete &lt;код&gt;", parse_mode="HTML"); return
    await db.execute("DELETE FROM promo_codes WHERE code=?", (command.args.strip(),))
    await db.commit()
    await message.answer(f"✅ Промокод {command.args.strip()} удалён")

@router.message(Command("promo"))
async def activate_promo(message: Message, command: CommandObject):
    user_id = message.from_user.id
    if not command.args: await message.answer("Используйте: /promo &lt;код&gt;"); return
    code = command.args.strip()
    async with db.execute("SELECT amount, activations_left, expires FROM promo_codes WHERE code=?", (code,)) as c:
        promo = await c.fetchone()
    if not promo: await message.answer("❌ Промокод не найден"); return
    if promo[1] <= 0: await message.answer("❌ Промокод исчерпан"); return
    if datetime.fromisoformat(promo[2]) < datetime.now(): await message.answer("❌ Срок действия истёк"); return
    async with db.execute("SELECT COUNT(*) FROM promo_uses WHERE user_id=? AND code=?", (user_id, code)) as c:
        if (await c.fetchone())[0] > 0: await message.answer("❌ Вы уже использовали этот промокод"); return
    await update_balance(user_id, promo[0])
    await db.execute("UPDATE promo_codes SET activations_left = activations_left - 1 WHERE code=?", (code,))
    await db.execute("INSERT INTO promo_uses (user_id, code) VALUES (?,?)", (user_id, code))
    await db.commit()
    await message.answer(f"✅ Промокод активирован! +{promo[0]}$")

@router.message(Command("setbizlimit"))
async def set_biz_limit(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    args = command.args.split() if command.args else []
    if len(args) < 2: await message.answer("❌ /setbizlimit &lt;тип&gt; &lt;число&gt;", parse_mode="HTML"); return
    biz_type, max_count = args[0], int(args[1])
    if biz_type not in BUSINESS_TYPES: await message.answer("❌ Неверный тип"); return
    await db.execute("INSERT OR REPLACE INTO biz_limits (biz_type, max_count) VALUES (?,?)", (biz_type, max_count))
    await db.commit()
    await message.answer(f"✅ Лимит {biz_type} установлен на {max_count}")

@router.message(Command("broadcast"))
async def admin_broadcast(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    if not command.args: await message.answer("❌ /broadcast &lt;текст&gt;", parse_mode="HTML"); return
    text = command.args.strip()
    users = await get_all_user_ids()
    ok = 0
    for uid in users:
        try:
            await bot.send_message(uid, f"📢 <b>Сообщение администратора:</b>\n\n{text}", parse_mode="HTML")
            ok += 1
        except Exception as e:
            logger.warning(f"Не доставлено {uid}: {e}")
    await message.answer(f"✅ Рассылка завершена: {ok}/{len(users)}.")

@router.message(Command("listusers"))
async def admin_listusers(message: Message):
    if message.from_user.id != ADMIN_ID: return
    async with db.execute("SELECT game_id, username, balance FROM users ORDER BY game_id LIMIT 20") as c:
        rows = await c.fetchall()
    if not rows: await message.answer("📭 Нет игроков."); return
    text = "📋 <b>Игроки (первые 20):</b>\n\n"
    for gid, name, bal in rows:
        safe_name = html_mod.escape(name or "Неизвестный")
        text += f"🆔{gid} – {safe_name} – {bal:,}$\n"
    await message.answer(text, parse_mode="HTML")

@router.message(Command("addbalanceall"))
async def admin_addbalanceall(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    if not command.args: await message.answer("❌ /addbalanceall &lt;сумма&gt;", parse_mode="HTML"); return
    try: amount = int(command.args.strip())
    except: await message.answer("❌ Сумма должна быть числом."); return
    await db.execute("UPDATE users SET balance = balance + ?", (amount,))
    await db.commit()
    await message.answer(f"✅ Всем игрокам выдано по {amount}$.")

@router.message(Command("setname"))
async def admin_setname(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    args = command.args.split(maxsplit=1) if command.args else []
    if len(args) < 2: await message.answer("❌ /setname &lt;game_id&gt; &lt;имя&gt;", parse_mode="HTML"); return
    tg = await resolve_target_id(args[0])
    if not tg: await message.answer("❌ Игрок не найден."); return
    new_name = args[1]
    await db.execute("UPDATE users SET username=? WHERE user_id=?", (new_name, tg))
    await db.commit()
    await message.answer(f"✅ Имя игрока {args[0]} изменено на «{html_mod.escape(new_name)}».")

# ------------------------------------------------------------
# 🎯 АУКЦИОН
# ------------------------------------------------------------
@router.callback_query(F.data.startswith("sell_auction:"))
async def sell_auction_start(call: CallbackQuery, state: FSMContext):
    biz_id = int(call.data.split(":")[1])
    await state.update_data(auction_biz_id=biz_id)
    await call.message.edit_text("Введите стартовую цену (минимум 50% от номинала).")
    await state.set_state(AuctionCreate.waiting_for_price)
    await call.answer()

@router.message(StateFilter(AuctionCreate.waiting_for_price))
async def auction_price(message: Message, state: FSMContext):
    try:
        price = int(message.text.strip())
    except:
        await message.answer("Введите целое число."); return
    data = await state.get_data()
    biz_id = data["auction_biz_id"]
    async with db.execute("SELECT price FROM businesses WHERE id=?", (biz_id,)) as c:
        row = await c.fetchone()
        if not row:
            await message.answer("❌ Бизнес не найден"); await state.clear(); return
        base_price = row[0]
    min_price = int(base_price * 0.5)
    if price < min_price:
        await message.answer(f"Минимальная цена: {min_price}$"); return
    await state.update_data(auction_price=price)
    await message.answer("Введите длительность аукциона в часах (от 1 до 24):")
    await state.set_state(AuctionCreate.waiting_for_duration)

@router.message(StateFilter(AuctionCreate.waiting_for_duration))
async def auction_duration(message: Message, state: FSMContext):
    try:
        hours = int(message.text.strip())
        if hours < 1 or hours > 24:
            raise ValueError
    except:
        await message.answer("Введите целое число от 1 до 24."); return
    data = await state.get_data()
    biz_id = data["auction_biz_id"]
    price = data["auction_price"]
    seller_id = message.from_user.id
    end_time = datetime.now() + timedelta(hours=hours)
    await db.execute(
        "INSERT INTO auctions (business_id, seller_id, start_price, current_bid, current_bidder_id, end_time) VALUES (?,?,?,?,?,?)",
        (biz_id, seller_id, price, price, None, end_time.isoformat())
    )
    await db.commit()
    await state.clear()
    await message.answer(f"✅ Бизнес выставлен на аукцион!\nСтарт: {price}$\nОкончание: {end_time.strftime('%d.%m %H:%M')}")

@router.message(F.text == "🔨 Аукцион")
async def auction_list(message: Message):
    async with db.execute(
        "SELECT a.id, b.name, a.start_price, a.current_bid, a.end_time, a.seller_id FROM auctions a JOIN businesses b ON a.business_id=b.id WHERE a.active=1"
    ) as c:
        auctions = await c.fetchall()
    if not auctions:
        await message.answer("📭 Нет активных аукционов."); return
    text = "🔨 <b>Аукционы</b>\n\n"
    for a in auctions:
        aid, bname, start, cur, end, seller = a
        time_left = datetime.fromisoformat(end) - datetime.now()
        hours = int(time_left.total_seconds() // 3600)
        text += f"#{aid} {bname} | Начало: {start}$ | Текущая: {cur}$ | Осталось: {hours}ч\n"
    await message.answer(text, parse_mode="HTML")
    await message.answer("Чтобы сделать ставку, напишите: /bid &lt;номер аукциона&gt; &lt;сумма&gt;", parse_mode="HTML")

@router.message(Command("bid"))
async def place_bid(message: Message, command: CommandObject):
    args = command.args.split() if command.args else []
    if len(args) != 2:
        await message.answer("❌ Используйте: /bid &lt;id&gt; &lt;сумма&gt;", parse_mode="HTML"); return
    aid = args[0]
    try:
        amount = int(args[1])
    except:
        await message.answer("Сумма должна быть числом."); return
    async with db.execute("SELECT * FROM auctions WHERE id=? AND active=1", (aid,)) as c:
        auction = await c.fetchone()
    if not auction:
        await message.answer("❌ Аукцион не найден или завершён."); return
    if amount <= auction[3]:
        await message.answer(f"❌ Ставка должна быть выше текущей ({auction[3]}$)."); return
    user = await get_user(message.from_user.id)
    if user[2] < amount:
        await message.answer("💸 Недостаточно средств."); return
    if auction[4] is not None:
        await update_balance(auction[4], auction[3])
    await update_balance(message.from_user.id, -amount)
    await db.execute("UPDATE auctions SET current_bid=?, current_bidder_id=? WHERE id=?", (amount, message.from_user.id, aid))
    await db.commit()
    await message.answer(f"✅ Ваша ставка {amount}$ принята!")

async def finish_auctions():
    while True:
        try:
            now = datetime.now().isoformat()
            async with db.execute("SELECT id, business_id, seller_id, current_bid, current_bidder_id FROM auctions WHERE active=1 AND end_time <= ?", (now,)) as c:
                expired = await c.fetchall()
            for a in expired:
                aid, biz_id, seller, cur, bidder = a
                if bidder is not None:
                    await db.execute("UPDATE businesses SET user_id=? WHERE id=?", (bidder, biz_id))
                    await update_balance(seller, cur)
                    try:
                        await bot.send_message(bidder, f"🏆 Вы выиграли аукцион #{aid}! Бизнес теперь ваш.")
                        await bot.send_message(seller, f"💰 Ваш бизнес продан за {cur}$.")
                    except: pass
                else:
                    try:
                        await bot.send_message(seller, "📭 Ваш аукцион завершился без ставок.")
                    except: pass
                await db.execute("UPDATE auctions SET active=0 WHERE id=?", (aid,))
                await db.commit()
        except Exception as e:
            logger.error(f"Ошибка завершения аукционов: {e}")
        await asyncio.sleep(30)

# ------------------------------------------------------------
# Запуск
# ------------------------------------------------------------
async def main():
    global db
    db = await aiosqlite.connect(DB_PATH)
    try:
        await ensure_db_schema()
        logger.info("БД готова, запуск бота...")
        asyncio.create_task(finish_auctions())
        await dp.start_polling(bot)
    finally:
        await db.close()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())