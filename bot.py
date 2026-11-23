import re
import json
import os
import random
import math
import time
from datetime import datetime, timezone
from collections import deque
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Wedge
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, FSInputFile, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
import asyncio

# ===== НАСТРОЙКИ =====
BOT_TOKEN = "9002477184:AAEmakeUk87LgY7xkhogFRCXVwZLBbn8f04"
DB_FILE = 'game_state.json'

RINGS = 9
CENTER_SECTORS = 8
SECTORS = 24
HERO_POWER = 3
RESPAWN_TIME = 3600
MOVE_RADIUS = 2

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ===== СОСТОЯНИЯ =====
class CreateCountry(StatesGroup):
    name = State()
    color = State()
    sector = State()

class AttackFlow(StatesGroup):
    choose_src = State()
    choose_amount = State()
    choose_dst = State()

class MoveFlow(StatesGroup):
    choose_src = State()
    choose_amount = State()
    choose_dst = State()

# ===== БАЗА ДАННЫХ =====
def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {
        "countries": {},
        "sectors": {},
        "users": {},
        "worlds": {},
        "soldiers": {},   # { cid: { "s1": {"sector": "R1-S1", "acted": "2026-11-23"}, ... } }
        "heroes": {},
        "pending_attacks": {},
        "last_day_reset": ""
    }

def save_db(db):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

db = load_db()

# ===== УТИЛИТЫ =====
def current_day_id():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def get_user_country(user_id):
    return db["users"].get(str(user_id))

def is_president(user_id, cid):
    return db["countries"][cid]["president"] == str(user_id)

def get_hero(user_id):
    return db["heroes"].get(str(user_id))

def main_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🌍 Создать фракцию"), KeyboardButton(text="🤝 Присоединиться")],
            [KeyboardButton(text="🏛 Моя страна"), KeyboardButton(text="🗺 Карта")],
            [KeyboardButton(text="⚔️ Атаковать"), KeyboardButton(text="🚚 Переместить")],
            [KeyboardButton(text="🏭 Произвести"), KeyboardButton(text="📊 Гарнизоны")],
            [KeyboardButton(text="🦸 Мой герой"), KeyboardButton(text="🏆 Лидеры")],
            [KeyboardButton(text="📖 Помощь")],
        ],
        resize_keyboard=True
    )

# ===== МИГРАЦИЯ СТАРЫХ ДАННЫХ =====
def migrate_old_data():
    """Если в базе старый формат armies — конвертируем в soldiers."""
    if "armies" in db and db["armies"]:
        day = current_day_id()
        for cid, sectors in db["armies"].items():
            if cid not in db["soldiers"]:
                db["soldiers"][cid] = {}
            counter = 0
            for sector, data in sectors.items():
                if isinstance(data, int):
                    count = data
                else:
                    count = data.get("count", 0)
                for _ in range(count):
                    counter += 1
                    sid = f"s{counter}"
                    db["soldiers"][cid][sid] = {"sector": sector, "acted": ""}
        del db["armies"]
        save_db(db)
migrate_old_data()

def reset_actions():
    """Сбрасывает acted у всех солдат, если день сменился."""
    day = current_day_id()
    if db.get("last_day_reset") == day:
        return
    for cid in db["soldiers"]:
        for sid, s in db["soldiers"][cid].items():
            s["acted"] = ""
    db["last_day_reset"] = day
    save_db(db)

def next_soldier_id(cid):
    """Возвращает следующий свободный ID солдата для страны."""
    if cid not in db["soldiers"]:
        db["soldiers"][cid] = {}
    existing = set(db["soldiers"][cid].keys())
    i = 1
    while f"s{i}" in existing:
        i += 1
    return f"s{i}"

# ===== ГЕОМЕТРИЯ =====
def sector_id(ring, sec):
    return f"R{ring}-S{sec+1}"

def parse_sector(sid):
    m = re.match(r'R(\d+)-S(\d+)', sid)
    if not m: return None
    r = int(m.group(1))
    s = int(m.group(2)) - 1
    if r == 0:
        if 0 <= s < CENTER_SECTORS: return (0, s)
    elif 1 <= r <= 8:
        if 0 <= s < SECTORS: return (r, s)
    return None

def all_sectors():
    res = [sector_id(0, s) for s in range(CENTER_SECTORS)]
    for r in range(1, RINGS):
        for s in range(SECTORS):
            res.append(sector_id(r, s))
    return res

def neighbors(sid):
    p = parse_sector(sid)
    if not p: return []
    r, s = p
    res = []
    if r == 0:
        res.append(sector_id(0, (s-1) % CENTER_SECTORS))
        res.append(sector_id(0, (s+1) % CENTER_SECTORS))
        base = s * (SECTORS // CENTER_SECTORS)
        for i in range(SECTORS // CENTER_SECTORS):
            res.append(sector_id(1, (base + i) % SECTORS))
    elif r == 1:
        center_idx = s * CENTER_SECTORS // SECTORS
        res.append(sector_id(0, center_idx % CENTER_SECTORS))
        res.append(sector_id(2, s))
        res.append(sector_id(1, (s-1) % SECTORS))
        res.append(sector_id(1, (s+1) % SECTORS))
    else:
        if r > 1: res.append(sector_id(r-1, s))
        if r < RINGS-1: res.append(sector_id(r+1, s))
        res.append(sector_id(r, (s-1) % SECTORS))
        res.append(sector_id(r, (s+1) % SECTORS))
    return res

def bfs_within_radius(src, radius):
    visited = {src: 0}
    q = deque([src])
    while q:
        cur = q.popleft()
        d = visited[cur]
        if d >= radius: continue
        for nb in neighbors(cur):
            if nb not in visited:
                visited[nb] = d + 1
                q.append(nb)
    return set(visited.keys())

def is_neighbor(src, dst):
    return dst in neighbors(src)

# ===== ХЕЛПЕРЫ ДЛЯ СОЛДАТ =====
def get_soldiers_in_sector(cid, sector):
    """Возвращает список (sid, data) солдат в секторе."""
    res = []
    for sid, s in db["soldiers"].get(cid, {}).items():
        if s["sector"] == sector:
            res.append((sid, s))
    return res

def count_available_in_sector(cid, sector):
    day = current_day_id()
    return sum(1 for sid, s in get_soldiers_in_sector(cid, sector) if s["acted"] != day)

def count_total_in_sector(cid, sector):
    return len(get_soldiers_in_sector(cid, sector))

def take_soldiers(cid, sector, amount):
    """Берёт до `amount` доступных солдат из сектора, помечает acted."""
    day = current_day_id()
    taken = []
    for sid, s in get_soldiers_in_sector(cid, sector):
        if s["acted"] != day and len(taken) < amount:
            s["acted"] = day
            taken.append(sid)
    return taken

def find_soldier(cid, sid):
    return db["soldiers"].get(cid, {}).get(sid)

# ===== КАРТА =====
def draw_map():
    fig, ax = plt.subplots(figsize=(14, 14), subplot_kw={'aspect': 'equal'})
    ax.set_xlim(-1.1, 1.1)
    ax.set_ylim(-1.1, 1.1)
    ax.axis('off')
    center_radius = 1 / RINGS
    # Центр
    for s in range(CENTER_SECTORS):
        theta1 = s * (360 / CENTER_SECTORS)
        theta2 = (s + 1) * (360 / CENTER_SECTORS)
        sid = sector_id(0, s)
        info = db["sectors"].get(sid)
        color = "#F0F0F0"
        if info and info.get("owner"):
            country = db["countries"].get(info["owner"])
            color = country["color"] if country else "#CCCCCC"
        wedge = Wedge((0, 0), center_radius, theta1, theta2, width=center_radius,
                      facecolor=color, edgecolor='gray', linewidth=0.6)
        ax.add_patch(wedge)
        mid_theta = (theta1 + theta2) / 2
        mid_r = center_radius / 2
        x0 = mid_r * math.cos(math.radians(mid_theta))
        y0 = mid_r * math.sin(math.radians(mid_theta))
        tx = -math.sin(math.radians(mid_theta))
        ty = math.cos(math.radians(mid_theta))
        total_army = sum(
            len([1 for sdata in db["soldiers"].get(c, {}).values() if sdata["sector"] == sid])
            for c in db["soldiers"]
        )
        if total_army > 0:
            ax.plot(x0 - 0.015 * tx, y0 - 0.015 * ty, 'ko', markersize=4, zorder=8)
        if info and info.get("owner"):
            c_data = db["countries"].get(info["owner"])
            if c_data and c_data.get("capital") == sid:
                ax.plot(x0, y0, marker='*', color='gold', markeredgecolor='black',
                        markersize=16, zorder=10)
        heroes_here = [h for h in db["heroes"].values() if h.get("sector") == sid and h.get("alive")]
        if heroes_here:
            ax.plot(x0 + 0.015 * tx, y0 + 0.015 * ty, marker='D', color='lime',
                    markeredgecolor='black', markersize=7, zorder=9)
    # Внешние кольца
    for ring in range(1, RINGS):
        r_outer = ring / RINGS
        r_inner = (ring - 1) / RINGS if ring > 1 else center_radius
        for sec in range(SECTORS):
            theta1 = sec * (360 / SECTORS)
            theta2 = (sec + 1) * (360 / SECTORS)
            sid = sector_id(ring, sec)
            info = db["sectors"].get(sid)
            color = "#F0F0F0"
            if info and info.get("owner"):
                country = db["countries"].get(info["owner"])
                color = country["color"] if country else "#CCCCCC"
            wedge = Wedge((0, 0), r_outer, theta1, theta2, width=r_outer - r_inner,
                          facecolor=color, edgecolor='gray', linewidth=0.6)
            ax.add_patch(wedge)
            mid_theta = (theta1 + theta2) / 2
            mid_r = (r_outer + r_inner) / 2
            x0 = mid_r * math.cos(math.radians(mid_theta))
            y0 = mid_r * math.sin(math.radians(mid_theta))
            tx = -math.sin(math.radians(mid_theta))
            ty = math.cos(math.radians(mid_theta))
            total_army = sum(
                len([1 for sdata in db["soldiers"].get(c, {}).values() if sdata["sector"] == sid])
                for c in db["soldiers"]
            )
            if total_army > 0:
                ax.plot(x0 - 0.025 * tx, y0 - 0.025 * ty, 'ko', markersize=4, zorder=8)
            if info and info.get("owner"):
                c_data = db["countries"].get(info["owner"])
                if c_data and c_data.get("capital") == sid:
                    ax.plot(x0, y0, marker='*', color='gold', markeredgecolor='black',
                            markersize=18, zorder=10)
            heroes_here = [h for h in db["heroes"].values() if h.get("sector") == sid and h.get("alive")]
            if heroes_here:
                ax.plot(x0 + 0.025 * tx, y0 + 0.025 * ty, marker='D', color='lime',
                        markeredgecolor='black', markersize=8, zorder=9)
    plt.savefig('map.png', dpi=120, bbox_inches='tight', transparent=True)
    plt.close()
    return 'map.png'

# ===== СТАРТ =====
@dp.message(Command("start"))
async def cmd_start(message: Message):
    reset_actions()
    user_id = str(message.from_user.id)
    cid = get_user_country(user_id)
    if cid:
        country = db["countries"][cid]
        role = "👑 Президент" if is_president(user_id, cid) else "🦸 Герой"
        await message.answer(
            f'👋 С возвращением, <b>{message.from_user.first_name}</b>!\n\n'
            f'Вы в стране <b>{country["name"]}</b>.\n'
            f'Ваша роль: {role}',
            reply_markup=main_menu_kb(),
            parse_mode=ParseMode.HTML
        )
    else:
        await message.answer(
            f'👋 Привет, <b>{message.from_user.first_name}</b>!\n\n'
            f'🎮 Игра по захвату территорий.\n'
            f'Выбери действие:',
            reply_markup=main_menu_kb(),
            parse_mode=ParseMode.HTML
        )

# ===== СОЗДАНИЕ =====
@dp.message(F.text == "🌍 Создать фракцию")
async def btn_create(message: Message, state: FSMContext):
    if get_user_country(message.from_user.id):
        await message.answer('❌ Вы уже в стране!'); return
    await state.set_state(CreateCountry.name)
    await message.answer('🏛 <b>Создание фракции</b>\n\nВведите название:', parse_mode=ParseMode.HTML)

@dp.message(CreateCountry.name)
async def create_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 2 or len(name) > 30:
        await message.answer('❌ 2-30 символов:'); return
    if any(c["name"].lower() == name.lower() for c in db["countries"].values()):
        await message.answer('❌ Уже есть такая страна:'); return
    await state.update_data(name=name)
    await state.set_state(CreateCountry.color)
    await message.answer('🎨 Цвет HEX (<code>#FF0000</code>):', parse_mode=ParseMode.HTML)

@dp.message(CreateCountry.color)
async def create_color(message: Message, state: FSMContext):
    color = message.text.strip()
    if not re.match(r'^#[0-9A-Fa-f]{6}$', color):
        await message.answer('❌ Формат <code>#FF0000</code>:'); return
    await state.update_data(color=color)
    await state.set_state(CreateCountry.sector)
    await message.answer('🏙 Сектор столицы (<code>R0-S1</code>…<code>R8-S24</code>):', parse_mode=ParseMode.HTML)

@dp.message(CreateCountry.sector)
async def create_sector(message: Message, state: FSMContext):
    sector = message.text.strip().upper()
    valid = all_sectors()
    if sector not in valid:
        await message.answer('❌ Неверный сектор.'); return
    if db["sectors"].get(sector, {}).get("owner"):
        await message.answer(f'❌ Сектор <code>{sector}</code> занят!', parse_mode=ParseMode.HTML); return

    data = await state.get_data()
    user_id = str(message.from_user.id)
    cid = str(len(db["countries"]) + 1)

    db["countries"][cid] = {
        "name": data['name'], "color": data['color'], "president": user_id,
        "members": [user_id], "capital": sector
    }
    db["users"][user_id] = cid
    db["soldiers"][cid] = {"s1": {"sector": sector, "acted": ""}}
    db["sectors"][sector] = {"owner": cid, "status": "captured"}
    db["worlds"][sector] = {"type": "людской", "country": cid}
    save_db(db)
    await state.clear()
    await message.answer(
        f'🎉 Фракция <b>{data["name"]}</b> создана!\n'
        f'👑 Вы президент.\n'
        f'🏙 Столица: <code>{sector}</code> (1 солдат)\n'
        f'⚠️ Как президент, вы не имеете героя.',
        reply_markup=main_menu_kb(),
        parse_mode=ParseMode.HTML
    )

# ===== ПРИСОЕДИНЕНИЕ =====
@dp.message(F.text == "🤝 Присоединиться")
async def btn_join(message: Message):
    if get_user_country(message.from_user.id):
        await message.answer('❌ Вы уже в стране!'); return
    if not db["countries"]:
        await message.answer('❌ Пока нет стран.'); return
    buttons = []
    for cid, c in db["countries"].items():
        buttons.append([InlineKeyboardButton(
            text=f'🏛 {c["name"]} ({len(c["members"])} чел.)',
            callback_data=f'join_{cid}'
        )])
    await message.answer('🤝 <b>Выберите страну:</b>',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith('join_'))
async def join_callback(callback: CallbackQuery):
    cid = callback.data.split('_')[1]
    user_id = str(callback.from_user.id)
    if get_user_country(user_id):
        await callback.answer('Вы уже в стране!', show_alert=True); return
    if cid not in db["countries"]:
        await callback.answer('Страна не найдена', show_alert=True); return

    country = db["countries"][cid]
    db["countries"][cid]["members"].append(user_id)
    db["users"][user_id] = cid
    db["heroes"][user_id] = {"sector": country["capital"], "alive": True, "respawn_at": 0}
    save_db(db)
    await callback.message.edit_text(
        f'✅ Вы присоединились к <b>{country["name"]}</b>!\n'
        f'🦸 Вы герой в столице <code>{country["capital"]}</code>.',
        parse_mode=ParseMode.HTML)
    await callback.answer()

# ===== ГЕРОЙ =====
@dp.message(F.text == "🦸 Мой герой")
async def btn_hero(message: Message):
    user_id = str(message.from_user.id)
    hero = get_hero(user_id)
    if not hero:
        await message.answer('👑 Вы президент — у вас нет героя.')
        return
    cid = get_user_country(user_id)
    country = db["countries"].get(cid, {})
    if not hero["alive"]:
        remaining = max(0, hero.get("respawn_at", 0) - int(time.time()))
        await message.answer(
            f'💀 <b>Герой мёртв.</b>\n'
            f'⏳ Воскрешение: {remaining // 60} мин {remaining % 60} сек.',
            parse_mode=ParseMode.HTML)
        return
    await message.answer(
        f'🦸 <b>Герой</b>\n'
        f'📛 {message.from_user.first_name}\n'
        f'🏛 {country.get("name", "—")}\n'
        f'📍 Сектор: <code>{hero["sector"]}</code>\n'
        f'💪 Сила: {HERO_POWER}\n\n'
        f'<code>.двигаться R1-S5</code>\n'
        f'<code>.атаковать R1-S5</code>',
        parse_mode=ParseMode.HTML)

@dp.message(F.text.regexp(r'^\.двигаться\s+(R\d+-S\d+)$'))
async def move_hero(message: Message):
    sector = message.text.split()[1].upper()
    user_id = str(message.from_user.id)
    hero = get_hero(user_id)
    if not hero or not hero["alive"]:
        await message.answer('❌ Герой недоступен.'); return
    if sector not in all_sectors():
        await message.answer('❌ Неверный сектор.'); return
    cid = get_user_country(user_id)
    owner = db["sectors"].get(sector, {}).get("owner")
    if owner and owner != cid:
        await message.answer(f'❌ Сектор <code>{sector}</code> чужой.', parse_mode=ParseMode.HTML); return
    hero["sector"] = sector
    save_db(db)
    await message.answer(f'🚶 Герой в <code>{sector}</code>.', parse_mode=ParseMode.HTML)

# ===== АТАКА ГЕРОЕМ =====
@dp.message(F.text.regexp(r'^\.атаковать\s+(R\d+-S\d+)$'))
async def hero_attack_cmd(message: Message):
    sector = message.text.split()[1].upper()
    user_id = str(message.from_user.id)
    hero = get_hero(user_id)
    if not hero or not hero["alive"]:
        await message.answer('❌ Герой недоступен.'); return
    cid = get_user_country(user_id)
    owner = db["sectors"].get(sector, {}).get("owner")
    if owner == cid:
        await message.answer('❌ Это ваш сектор.'); return
    if hero["sector"] != sector:
        await message.answer(f'❌ Герой в <code>{hero["sector"]}</code>. Переместитесь.', parse_mode=ParseMode.HTML); return

    pending = db["pending_attacks"].setdefault(sector, [])
    pending.append({"cid": cid, "hero": user_id, "power": HERO_POWER, "timestamp": int(time.time())})
    save_db(db)
    await message.answer(f'⚔️ Атака героя на <code>{sector}</code> запланирована!', parse_mode=ParseMode.HTML)
    other = [a for a in pending if a["cid"] != cid]
    if other:
        await resolve_pending_attacks(sector, message)
    else:
        await resolve_single_attack(sector, cid, message)

# ===== ПЕРЕМЕЩЕНИЕ (кнопка) =====
@dp.message(F.text == "🚚 Переместить")
async def btn_move(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)
    cid = get_user_country(user_id)
    if not cid:
        await message.answer('❌ Вы не в стране.'); return
    if not is_president(user_id, cid):
        await message.answer('❌ Только президент.'); return

    sectors_with_soldiers = set()
    for sid, s in db["soldiers"].get(cid, {}).items():
        sectors_with_soldiers.add(s["sector"])
    if not sectors_with_soldiers:
        await message.answer('❌ У вас нет войск.'); return

    buttons = []
    for sector in sorted(sectors_with_soldiers):
        total = count_total_in_sector(cid, sector)
        buttons.append([InlineKeyboardButton(
            text=f"📍 {sector} — {total} солдат",
            callback_data=f"mv_src_{sector}"
        )])

    await state.set_state(MoveFlow.choose_src)
    await message.answer(
        '🚚 <b>Перемещение</b>\n\n'
        'Выберите сектор, <b>откуда</b> перебросить:',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode=ParseMode.HTML
    )

@dp.callback_query(F.data.startswith("mv_src_"), MoveFlow.choose_src)
async def mv_src_chosen(callback: CallbackQuery, state: FSMContext):
    sector = callback.data.split("_", 2)[2]
    user_id = str(callback.from_user.id)
    cid = get_user_country(user_id)
    total = count_total_in_sector(cid, sector)
    await state.update_data(src=sector, max_amount=total)
    await state.set_state(MoveFlow.choose_amount)
    await callback.message.edit_text(
        f'📍 Источник: <code>{sector}</code>\n'
        f'Всего солдат: <b>{total}</b>\n\n'
        f'Сколько перебросить? (1–{total})',
        parse_mode=ParseMode.HTML)
    await callback.answer()

@dp.message(MoveFlow.choose_amount)
async def mv_amount_chosen(message: Message, state: FSMContext):
    try:
        amount = int(message.text.strip())
    except ValueError:
        await message.answer('❌ Введите число.'); return
    data = await state.get_data()
    max_amount = data.get("max_amount", 0)
    if amount < 1 or amount > max_amount:
        await message.answer(f'❌ Введите 1–{max_amount}.'); return
    await state.update_data(amount=amount)
    await state.set_state(MoveFlow.choose_dst)

    src = data["src"]
    cid = get_user_country(message.from_user.id)
    reachable = bfs_within_radius(src, MOVE_RADIUS)
    my_sectors = [s for s, info in db["sectors"].items() if info.get("owner") == cid]
    buttons = []
    for nb in sorted(reachable):
        if nb in my_sectors and nb != src:
            total = count_total_in_sector(cid, nb)
            buttons.append([InlineKeyboardButton(
                text=f"🏛 {nb} (там {total})",
                callback_data=f"mv_dst_{nb}"
            )])
    if not buttons:
        await message.answer('❌ Нет доступных своих секторов в радиусе.')
        await state.clear()
        return
    await message.answer(
        f'🎯 Куда перебросить <b>{amount}</b> солдат из <code>{src}</code>?',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith("mv_dst_"), MoveFlow.choose_dst)
async def mv_dst_chosen(callback: CallbackQuery, state: FSMContext):
    dst = callback.data.split("_", 2)[2]
    user_id = str(callback.from_user.id)
    cid = get_user_country(user_id)
    data = await state.get_data()
    src = data["src"]
    amount = data["amount"]
    await state.clear()

    # Берём ЛЮБЫХ солдат (не только доступных) — перемещение не тратит ход
    soldiers = get_soldiers_in_sector(cid, src)
    if len(soldiers) < amount:
        await callback.message.edit_text('❌ Недостаточно солдат.', parse_mode=ParseMode.HTML)
        await callback.answer()
        return
    moved = 0
    for sid, s in soldiers:
        if moved >= amount: break
        s["sector"] = dst
        moved += 1
    save_db(db)
    await callback.message.edit_text(
        f'🚚 Перемещено <b>{moved}</b> солдат из <code>{src}</code> в <code>{dst}</code>.',
        parse_mode=ParseMode.HTML)
    await callback.answer()

# ===== КОМАНДА .переместить =====
@dp.message(F.text.regexp(r'^\.переместить\s+(R\d+-S\d+)\s+(R\d+-S\d+)\s+(\d+)$'))
async def move_troops(message: Message):
    parts = message.text.split()
    src, dst, amount = parts[1].upper(), parts[2].upper(), int(parts[3])
    user_id = str(message.from_user.id)
    cid = get_user_country(user_id)
    if not cid or not is_president(user_id, cid):
        await message.answer('❌ Только президент.'); return
    if amount <= 0:
        await message.answer('❌ Минимум 1.'); return
    if db["sectors"].get(src, {}).get("owner") != cid:
        await message.answer('❌ Источник не ваш.'); return
    if db["sectors"].get(dst, {}).get("owner") != cid:
        await message.answer('❌ Цель не ваша.'); return
    if dst not in bfs_within_radius(src, MOVE_RADIUS):
        await message.answer(f'❌ Слишком далеко (радиус {MOVE_RADIUS}).'); return
    soldiers = get_soldiers_in_sector(cid, src)
    if len(soldiers) < amount:
        await message.answer(f'❌ В <code>{src}</code> только {len(soldiers)} солдат.', parse_mode=ParseMode.HTML); return
    moved = 0
    for sid, s in soldiers:
        if moved >= amount: break
        s["sector"] = dst
        moved += 1
    save_db(db)
    await message.answer(f'🚚 Перемещено {moved} солдат: <code>{src}</code> → <code>{dst}</code>.', parse_mode=ParseMode.HTML)

# ===== АТАКА (кнопка) =====
@dp.message(F.text == "⚔️ Атаковать")
async def btn_attack(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)
    cid = get_user_country(user_id)
    if not cid:
        await message.answer('❌ Вы не в стране.'); return
    if not is_president(user_id, cid):
        await message.answer('❌ Только президент.'); return

    sectors_with_available = {}
    for sid, s in db["soldiers"].get(cid, {}).items():
        day = current_day_id()
        if s["acted"] != day:
            sectors_with_available[s["sector"]] = sectors_with_available.get(s["sector"], 0) + 1
    if not sectors_with_available:
        await message.answer('❌ Все солдаты уже сходили сегодня.'); return

    buttons = []
    for sector, avail in sorted(sectors_with_available.items()):
        total = count_total_in_sector(cid, sector)
        buttons.append([InlineKeyboardButton(
            text=f"📍 {sector} — {avail} готовы (из {total})",
            callback_data=f"atk_src_{sector}"
        )])
    await state.set_state(AttackFlow.choose_src)
    await message.answer(
        '⚔️ <b>Атака</b>\n\nВыберите сектор-источник:',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith("atk_src_"), AttackFlow.choose_src)
async def atk_src_chosen(callback: CallbackQuery, state: FSMContext):
    sector = callback.data.split("_", 2)[2]
    cid = get_user_country(callback.from_user.id)
    avail = count_available_in_sector(cid, sector)
    await state.update_data(src=sector, max_amount=avail)
    await state.set_state(AttackFlow.choose_amount)
    await callback.message.edit_text(
        f'📍 Источник: <code>{sector}</code>\n'
        f'Доступно солдат: <b>{avail}</b>\n\n'
        f'Сколько отправить в атаку? (1–{avail})',
        parse_mode=ParseMode.HTML)
    await callback.answer()

@dp.message(AttackFlow.choose_amount)
async def atk_amount_chosen(message: Message, state: FSMContext):
    try:
        amount = int(message.text.strip())
    except ValueError:
        await message.answer('❌ Введите число.'); return
    data = await state.get_data()
    max_amount = data.get("max_amount", 0)
    if amount < 1 or amount > max_amount:
        await message.answer(f'❌ Введите 1–{max_amount}.'); return
    await state.update_data(amount=amount)
    await state.set_state(AttackFlow.choose_dst)

    src = data["src"]
    cid = get_user_country(message.from_user.id)
    buttons = []
    for nb in neighbors(src):
        info = db["sectors"].get(nb, {})
        owner = info.get("owner")
        if owner == cid:
            continue
        if owner:
            country = db["countries"].get(owner, {})
            label = f"🏛 {country.get('name', '?')} ({nb})"
        else:
            label = f"🌫 Свободный ({nb})"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"atk_dst_{nb}")])
    if not buttons:
        await message.answer('❌ Нет доступных целей среди соседей.')
        await state.clear()
        return
    await message.answer(
        f'🎯 Отправляем <b>{amount}</b> солдат из <code>{src}</code>.\n\nВыберите цель:',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith("atk_dst_"), AttackFlow.choose_dst)
async def atk_dst_chosen(callback: CallbackQuery, state: FSMContext):
    dst = callback.data.split("_", 2)[2]
    user_id = str(callback.from_user.id)
    cid = get_user_country(user_id)
    data = await state.get_data()
    src, amount = data["src"], data["amount"]
    await state.clear()

    avail = count_available_in_sector(cid, src)
    if amount > avail:
        await callback.message.edit_text(f'❌ Доступно только {avail}.', parse_mode=ParseMode.HTML)
        await callback.answer(); return

    # Берём доступных солдат, помечаем acted, удаляем из источника
    taken = take_soldiers(cid, src, amount)
    if len(taken) < amount:
        await callback.message.edit_text('❌ Ошибка: не удалось взять солдат.', parse_mode=ParseMode.HTML)
        await callback.answer(); return
    # Удаляем из их текущего сектора (они пойдут в атаку)
    for sid in taken:
        del db["soldiers"][cid][sid]

    pending = db["pending_attacks"].setdefault(dst, [])
    pending.append({"cid": cid, "amount": len(taken), "from": src, "timestamp": int(time.time())})
    save_db(db)

    await callback.message.edit_text(
        f'⚔️ <b>{len(taken)}</b> солдат из <code>{src}</code> атакуют <code>{dst}</code>!',
        parse_mode=ParseMode.HTML)
    await callback.answer()

    other = [a for a in pending if a["cid"] != cid]
    if other:
        await resolve_pending_attacks(dst, callback.message)
    else:
        await resolve_single_attack(dst, cid, callback.message)

# ===== ГАРНИЗОНЫ =====
@dp.message(F.text == "📊 Гарнизоны")
async def btn_garrisons(message: Message):
    user_id = str(message.from_user.id)
    cid = get_user_country(user_id)
    if not cid:
        await message.answer('❌ Вы не в стране.'); return
    day = current_day_id()
    by_sector = {}
    for sid, s in db["soldiers"].get(cid, {}).items():
        sec = s["sector"]
        by_sector.setdefault(sec, {"total": 0, "ready": 0})
        by_sector[sec]["total"] += 1
        if s["acted"] != day:
            by_sector[sec]["ready"] += 1
    if not by_sector:
        await message.answer('📭 Нет солдат.'); return
    lines = ['📊 <b>Ваши гарнизоны</b>\n']
    ta, tr = 0, 0
    for sector, data in sorted(by_sector.items()):
        role = ""
        if sector in db["worlds"]:
            role = " 🏭" if db["worlds"][sector]["type"] == "кузня" else " 🏙"
        lines.append(f'📍 <code>{sector}</code>{role}\n   Всего: <b>{data["total"]}</b> | Готовы: <b>{data["ready"]}</b>')
        ta += data["total"]; tr += data["ready"]
    lines.append(f'\n<b>Итого:</b> {ta} солдат, {tr} готовы.')
    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)

# ===== РАЗРЕШЕНИЕ АТАК =====
async def resolve_pending_attacks(sector, message: Message):
    pending = db["pending_attacks"].get(sector, [])
    if not pending: return
    forces = {}
    hero_attacks = {}
    for a in pending:
        cid = a["cid"]
        forces[cid] = forces.get(cid, 0) + a.get("amount", 0) + a.get("power", 0)
        if "hero" in a:
            hero_attacks.setdefault(cid, []).append(a["hero"])
    if len(forces) == 1:
        await resolve_single_attack(sector, list(forces.keys())[0], message)
        return
    sorted_forces = sorted(forces.items(), key=lambda x: x[1], reverse=True)
    winner_cid, winner_power = sorted_forces[0]
    second_power = sorted_forces[1][1] if len(sorted_forces) > 1 else 0
    roll = random.randint(1, 20)
    ratio = winner_power / second_power if second_power > 0 else 99
    threshold = 5 if ratio >= 3 else 8 if ratio >= 2 else 10 if ratio >= 1.5 else 11
    winner_name = db["countries"][winner_cid]["name"]
    if roll >= threshold:
        db["sectors"][sector] = {"owner": winner_cid, "status": "captured"}
        for cid in forces:
            if cid != winner_cid:
                to_remove = [sid for sid, s in db["soldiers"].get(cid, {}).items() if s["sector"] == sector]
                for sid in to_remove:
                    del db["soldiers"][cid][sid]
        # Победитель получает солдат в сектор
        for _ in range(winner_power):
            sid = next_soldier_id(winner_cid)
            db["soldiers"][winner_cid][sid] = {"sector": sector, "acted": current_day_id()}
        for cid, heroes in hero_attacks.items():
            if cid != winner_cid:
                for h_id in heroes:
                    if h_id in db["heroes"]:
                        db["heroes"][h_id]["alive"] = False
                        db["heroes"][h_id]["respawn_at"] = int(time.time()) + RESPAWN_TIME
        db["pending_attacks"][sector] = []
        save_db(db)
        await message.answer(
            f'⚔️ <b>ВСТРЕЧНЫЙ БОЙ</b> за <code>{sector}</code>!\n'
            f'🏆 <b>{winner_name}</b> ({winner_power} vs {second_power})\n'
            f'🎲 {roll} (нужно {threshold})\n✅ Сектор захвачен!',
            parse_mode=ParseMode.HTML)
        await ask_sector_role(sector, winner_cid, message)
    else:
        for cid in forces:
            to_remove = [sid for sid, s in db["soldiers"].get(cid, {}).items() if s["sector"] == sector]
            for sid in to_remove:
                del db["soldiers"][cid][sid]
        for heroes in hero_attacks.values():
            for h_id in heroes:
                if h_id in db["heroes"]:
                    db["heroes"][h_id]["alive"] = False
                    db["heroes"][h_id]["respawn_at"] = int(time.time()) + RESPAWN_TIME
        db["pending_attacks"][sector] = []
        save_db(db)
        await message.answer(
            f'⚔️ <b>ВСТРЕЧНЫЙ БОЙ</b> за <code>{sector}</code>!\n'
            f'🏆 <b>{winner_name}</b> ({winner_power} vs {second_power})\n'
            f'🎲 {roll} (нужно {threshold})\n❌ Победитель проиграл!',
            parse_mode=ParseMode.HTML)

async def resolve_single_attack(sector, cid, message: Message):
    pending = db["pending_attacks"].get(sector, [])
    total_force = sum(a.get("amount", 0) + a.get("power", 0) for a in pending)
    owner = db["sectors"].get(sector, {}).get("owner")
    is_capital = db["countries"].get(owner, {}).get("capital") == sector if owner else False
    if not owner:
        db["sectors"][sector] = {"owner": cid, "status": "captured"}
        for _ in range(total_force):
            sid = next_soldier_id(cid)
            db["soldiers"][cid][sid] = {"sector": sector, "acted": current_day_id()}
        db["pending_attacks"][sector] = []
        save_db(db)
        await message.answer(f'🚩 <code>{sector}</code> присоединён.', parse_mode=ParseMode.HTML)
        await ask_sector_role(sector, cid, message)
        return
    enemy_soldiers = [sid for sid, s in db["soldiers"].get(owner, {}).items() if s["sector"] == sector]
    enemy_heroes_count = len([1 for uid, h in db["heroes"].items() if h.get("sector") == sector and h.get("alive") and db["users"].get(uid) == owner])
    total_enemy = len(enemy_soldiers) + enemy_heroes_count * HERO_POWER
    if total_enemy == 0:
        db["sectors"][sector] = {"owner": cid, "status": "captured"}
        for _ in range(total_force):
            sid = next_soldier_id(cid)
            db["soldiers"][cid][sid] = {"sector": sector, "acted": current_day_id()}
        extra = ""
        if is_capital:
            extra = f'\n💀 <b>СТОЛИЦА ПАЛА!</b> {db["countries"][owner]["name"]} уничтожена!'
            del db["countries"][owner]
            if owner in db["soldiers"]: del db["soldiers"][owner]
            for uid, c2 in list(db["users"].items()):
                if c2 == owner:
                    del db["users"][uid]
                    if uid in db["heroes"]: del db["heroes"][uid]
        db["pending_attacks"][sector] = []
        save_db(db)
        await message.answer(f'🚩 <code>{sector}</code> захвачен без боя!{extra}', parse_mode=ParseMode.HTML)
        await ask_sector_role(sector, cid, message)
        return
    ratio = total_force / total_enemy
    if ratio >= 3.0: threshold, t_str = 5, "5-20"
    elif ratio >= 2.0: threshold, t_str = 8, "8-20"
    elif ratio >= 1.5: threshold, t_str = 10, "10-20"
    elif ratio >= 1.0: threshold, t_str = 11, "11-20"
    else: threshold, t_str = 14, "14-20"
    roll = random.randint(1, 20)
    success = roll >= threshold
    if success:
        db["sectors"][sector] = {"owner": cid, "status": "captured"}
        for sid in enemy_soldiers:
            if sid in db["soldiers"].get(owner, {}):
                del db["soldiers"][owner][sid]
        for uid, h in list(db["heroes"].items()):
            if h.get("sector") == sector and h.get("alive") and db["users"].get(uid) == owner:
                h["alive"] = False
                h["respawn_at"] = int(time.time()) + RESPAWN_TIME
        for _ in range(total_force):
            sid = next_soldier_id(cid)
            db["soldiers"][cid][sid] = {"sector": sector, "acted": current_day_id()}
        extra = ""
        if is_capital:
            extra = f'\n💀 <b>СТОЛИЦА ПАЛА!</b> {db["countries"][owner]["name"]} уничтожена!'
            del db["countries"][owner]
            if owner in db["soldiers"]: del db["soldiers"][owner]
            for uid, c2 in list(db["users"].items()):
                if c2 == owner:
                    del db["users"][uid]
                    if uid in db["heroes"]: del db["heroes"][uid]
        db["pending_attacks"][sector] = []
        save_db(db)
        await message.answer(
            f'🎲 {roll} ({t_str})\n✅ <b>Успех!</b> <code>{sector}</code> захвачен!{extra}',
            parse_mode=ParseMode.HTML)
        await ask_sector_role(sector, cid, message)
    else:
        db["pending_attacks"][sector] = []
        save_db(db)
        await message.answer(
            f'🎲 {roll} ({t_str})\n❌ <b>Провал!</b> {total_force} солдат погибли.',
            parse_mode=ParseMode.HTML)

# ===== ВЫБОР РОЛИ =====
async def ask_sector_role(sector, cid, message: Message):
    if sector in db["worlds"]: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏭 Кузня", callback_data=f"role_kuznya_{sector}_{cid}")],
        [InlineKeyboardButton(text="🏙 Людской мир", callback_data=f"role_lyudi_{sector}_{cid}")],
        [InlineKeyboardButton(text="⏭ Оставить пустым", callback_data=f"role_none_{sector}_{cid}")]
    ])
    await message.answer(f'🎉 Сектор <code>{sector}</code> захвачен!\nВыберите роль:', reply_markup=kb, parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith('role_'))
async def role_callback(callback: CallbackQuery):
    parts = callback.data.split('_')
    role_type = parts[1]; sector = parts[2]; cid = parts[3]
    user_id = str(callback.from_user.id)
    if not is_president(user_id, cid):
        await callback.answer('Только президент!', show_alert=True); return
    if role_type == "none":
        await callback.message.edit_text(f'⏭ <code>{sector}</code> без роли.', parse_mode=ParseMode.HTML)
    else:
        db["worlds"][sector] = {"type": role_type, "country": cid}
        save_db(db)
        role_name = "кузня" if role_type == "kuznya" else "людской мир"
        await callback.message.edit_text(f'🏭 <code>{sector}</code> — <b>{role_name}</b>.', parse_mode=ParseMode.HTML)
    await callback.answer()

# ===== ПРОИЗВОДСТВО =====
@dp.message(F.text == "🏭 Произвести")
async def btn_produce(message: Message):
    user_id = str(message.from_user.id)
    cid = get_user_country(user_id)
    if not cid: await message.answer('❌ Вы не в стране.'); return
    if not is_president(user_id, cid): await message.answer('❌ Только президент.'); return
    kuzni = [s for s, w in db["worlds"].items() if w["country"] == cid and w["type"] == "кузня"]
    lyudi = [s for s, w in db["worlds"].items() if w["country"] == cid and w["type"] == "людской"]
    pairs = min(len(kuzni), len(lyudi))
    if pairs == 0:
        await message.answer('❌ Нужна хотя бы 1 кузня и 1 людской мир.'); return
    capital = db["countries"][cid]["capital"]
    for _ in range(pairs):
        sid = next_soldier_id(cid)
        db["soldiers"][cid][sid] = {"sector": capital, "acted": ""}
    save_db(db)
    await message.answer(
        f'⚔️ Произведено <b>{pairs}</b> солдат в столице <code>{capital}</code>!',
        parse_mode=ParseMode.HTML)

# ===== КАРТА =====
@dp.message(F.text == "🗺 Карта")
async def btn_map(message: Message):
    await message.answer('🗺 Генерирую карту...')
    path = draw_map()
    await message.answer_photo(FSInputFile(path), caption='Цвет = страна | ⚫ = войска | ⭐ = столица | 🟢 = герой')

# ===== МОЯ СТРАНА =====
@dp.message(F.text == "🏛 Моя страна")
async def btn_my_country(message: Message):
    user_id = str(message.from_user.id)
    cid = get_user_country(user_id)
    if not cid: await message.answer('❌ Вы не в стране.'); return
    c = db["countries"][cid]
    role = "👑 Президент" if is_president(user_id, cid) else "🦸 Герой"
    territories = [s for s, info in db["sectors"].items() if info.get("owner") == cid]
    kuzni = [s for s, w in db["worlds"].items() if w["country"] == cid and w["type"] == "кузня"]
    lyudi = [s for s, w in db["worlds"].items() if w["country"] == cid and w["type"] == "людской"]
    day = current_day_id()
    garrison_text = ""
    by_sector = {}
    for sid, s in db["soldiers"].get(cid, {}).items():
        by_sector.setdefault(s["sector"], {"total": 0, "ready": 0})
        by_sector[s["sector"]]["total"] += 1
        if s["acted"] != day:
            by_sector[s["sector"]]["ready"] += 1
    for sector, d in sorted(by_sector.items()):
        garrison_text += f'\n  • <code>{sector}</code>: {d["total"]} (готовы: {d["ready"]})'
    await message.answer(
        f'🏛 <b>{c["name"]}</b>\n'
        f'👑 Президент: <code>{c["president"]}</code>\n'
        f'👥 Участников: {len(c["members"])}\n'
        f'🗺 Территорий: {len(territories)}\n'
        f'🏭 Кузни: {len(kuzni)} | Людские: {len(lyudi)}\n'
        f'🏙 Столица: <code>{c.get("capital", "нет")}</code>\n'
        f'🎖 Роль: {role}\n\n'
        f'<b>⚔️ Гарнизоны:</b>{garrison_text or " нет"}',
        parse_mode=ParseMode.HTML)

# ===== ЛИДЕРЫ =====
@dp.message(F.text == "🏆 Лидеры")
async def btn_leaders(message: Message):
    countries_list = []
    for cid, c in db["countries"].items():
        territories = len([s for s, info in db["sectors"].items() if info.get("owner") == cid])
        total_army = len(db["soldiers"].get(cid, {}))
        heroes_alive = len([1 for uid, h in db["heroes"].items() if h.get("alive") and db["users"].get(uid) == cid])
        countries_list.append({
            "cid": cid, "name": c["name"],
            "territories": territories, "army": total_army,
            "heroes": heroes_alive, "members": len(c["members"])
        })
    if not countries_list:
        await message.answer('📊 Пока нет стран.'); return
    countries_list.sort(key=lambda x: (x["territories"], x["army"]), reverse=True)
    lines = ["🏆 <b>ДЕМОНСТРАЦИЯ СИЛЫ</b>\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, c in enumerate(countries_list):
        place = medals[i] if i < 3 else f"{i+1}."
        lines.append(
            f'{place} <b>{c["name"]}</b>\n'
            f'   🗺 Секторов: <b>{c["territories"]}</b> | '
            f'⚔️ Армия: <b>{c["army"]}</b>\n'
            f'   🦸 Героев: {c["heroes"]} | 👥 Участников: {c["members"]}'
        )
    await message.answer("\n\n".join(lines), parse_mode=ParseMode.HTML)

# ===== ПОМОЩЬ =====
@dp.message(F.text == "📖 Помощь")
async def btn_help(message: Message):
    await message.answer(
        "📖 <b>Команды:</b>\n\n"
        "<b>🌍 Страна:</b> кнопки «Создать» / «Присоединиться»\n\n"
        "<b>🦸 Герой:</b>\n"
        "<code>.двигаться R1-S5</code>\n"
        "<code>.атаковать R1-S5</code>\n\n"
        "<b>👑 Президент:</b>\n"
        "• Кнопка «⚔️ Атаковать»\n"
        "• Кнопка «🚚 Переместить»\n"
        "• Кнопка «🏭 Произвести»\n"
        "• Кнопка «📊 Гарнизоны»\n"
        "<code>.переместить R1-S5 R1-S6 3</code>\n"
        "<code>.назначить_мир R1-S5 кузня</code>\n\n"
        "⚔️ <b>Правила:</b>\n"
        "• Атака — только соседний сектор\n"
        "• Перемещение — радиус 2 по своим\n"
        "• 1 ход на солдата в день\n"
        "• Встречный бой при 2+ атакующих\n\n"
        "📐 <code>R0-S1</code>…<code>R8-S24</code>",
        parse_mode=ParseMode.HTML)

# ===== НАЗНАЧИТЬ МИР =====
@dp.message(F.text.regexp(r'^\.назначить_мир\s+(R\d+-S\d+)\s+(кузня|людской)$'))
async def assign_world(message: Message):
    parts = message.text.split()
    sector, world_type = parts[1].upper(), parts[2].lower()
    user_id = str(message.from_user.id)
    cid = get_user_country(user_id)
    if not cid or not is_president(user_id, cid):
        await message.answer('❌ Только президент.'); return
    if db["sectors"].get(sector, {}).get("owner") != cid:
        await message.answer('❌ Сектор не ваш!'); return
    if db["countries"][cid]["capital"] == sector:
        await message.answer('❌ Столица уже людской.'); return
    db["worlds"][sector] = {"type": world_type, "country": cid}
    save_db(db)
    await message.answer(f'🏭 <code>{sector}</code> — <b>{world_type}</b>.', parse_mode=ParseMode.HTML)

# ===== ЗАПУСК =====
async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
