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
from aiogram.types import Message, FSInputFile, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
import asyncio

# ===== НАСТРОЙКИ =====
BOT_TOKEN = '9002477184:AAEmakeUk87LgY7xkhogFRCXVwZLBbn8f04'
DB_FILE = 'game_state.json'

RINGS = 8
SECTORS = 24
HERO_POWER = 3
RESPAWN_TIME = 3600
MOVE_RADIUS = 2  # радиус перемещения по своей территории

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ===== СОСТОЯНИЯ =====
class CreateCountry(StatesGroup):
    name = State()
    color = State()
    sector = State()

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
        "armies": {},
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
            [KeyboardButton(text="⚔️ Атаковать"), KeyboardButton(text="🏭 Произвести")],
            [KeyboardButton(text="🦸 Мой герой"), KeyboardButton(text="🏆 Лидеры")],
            [KeyboardButton(text="📖 Помощь")],
        ],
        resize_keyboard=True
    )

# ===== НОРМАЛИЗАЦИЯ =====
def normalize_armies():
    changed = False
    for cid in list(db["armies"].keys()):
        for sector in list(db["armies"][cid].keys()):
            val = db["armies"][cid][sector]
            if isinstance(val, int):
                db["armies"][cid][sector] = {"count": val, "acted": []}
                changed = True
    if changed:
        save_db(db)
normalize_armies()

# ===== СБРОС =====
def reset_actions():
    day = current_day_id()
    if db.get("last_day_reset") == day:
        return
    for cid in db["armies"]:
        for sector in db["armies"][cid]:
            db["armies"][cid][sector]["acted"] = []
    db["last_day_reset"] = day
    save_db(db)

# ===== ГЕОМЕТРИЯ СЕКТОРОВ =====
def sector_id(ring, sec):
    """ring 0..RINGS-1, sec 0..SECTORS-1 -> 'R{n}-S{m}'"""
    return f"R{ring+1}-S{sec+1}"

def parse_sector(sid):
    """'R3-S5' -> (2, 4)"""
    m = re.match(r'R(\d+)-S(\d+)', sid)
    if not m: return None
    r = int(m.group(1)) - 1
    s = int(m.group(2)) - 1
    if 0 <= r < RINGS and 0 <= s < SECTORS:
        return (r, s)
    return None

def neighbors(sid):
    """Соседние сектора (общая граница)."""
    p = parse_sector(sid)
    if not p: return []
    r, s = p
    res = []
    # Внутреннее/внешнее кольцо
    if r > 0: res.append(sector_id(r-1, s))
    if r < RINGS-1: res.append(sector_id(r+1, s))
    # Соседи по углу (сектор слева/справа)
    res.append(sector_id(r, (s-1) % SECTORS))
    res.append(sector_id(r, (s+1) % SECTORS))
    return res

def bfs_within_radius(src, radius):
    """Все сектора в радиусе radius шагов от src (включая src)."""
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

# ===== КАРТА =====
def draw_map():
    fig, ax = plt.subplots(figsize=(12, 12), subplot_kw={'aspect': 'equal'})
    ax.set_xlim(-1.05, 1.05)
    ax.set_ylim(-1.05, 1.05)
    ax.axis('off')
    for ring in range(RINGS):
        r_outer = (ring + 1) / RINGS
        r_inner = ring / RINGS
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
            x = mid_r * math.cos(math.radians(mid_theta))
            y = mid_r * math.sin(math.radians(mid_theta))
            total_army = sum(
                data.get("count", 0) for c_data in db["armies"].values()
                for s, data in c_data.items() if s == sid
            )
            if total_army > 0:
                ax.plot(x, y, 'ko', markersize=4)
            if info and info.get("owner"):
                c_data = db["countries"].get(info["owner"])
                if c_data and c_data.get("capital") == sid:
                    ax.plot(x, y, marker='*', color='gold', markeredgecolor='black', markersize=16, zorder=10)
            heroes_here = [h for h in db["heroes"].values() if h.get("sector") == sid and h.get("alive")]
            if heroes_here:
                ax.plot(x, y, marker='D', color='lime', markeredgecolor='black', markersize=8, zorder=9)
    ax.add_patch(plt.Circle((0, 0), 1/RINGS, color='white', ec='gray', linewidth=1))
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
    await message.answer('🏙 Сектор столицы (<code>R1-S1</code>):', parse_mode=ParseMode.HTML)

@dp.message(CreateCountry.sector)
async def create_sector(message: Message, state: FSMContext):
    sector = message.text.strip().upper()
    valid = [f"R{r+1}-S{s+1}" for r in range(RINGS) for s in range(SECTORS)]
    if sector not in valid:
        await message.answer('❌ Неверный сектор:'); return
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
    db["armies"][cid] = {sector: {"count": 1, "acted": []}}
    db["sectors"][sector] = {"owner": cid, "status": "captured"}
    db["worlds"][sector] = {"type": "людской", "country": cid}
    db["heroes"][user_id] = {"sector": sector, "alive": True, "respawn_at": 0}
    
    save_db(db)
    await state.clear()
    await message.answer(
        f'🎉 Фракция <b>{data["name"]}</b> создана!\n'
        f'👑 Вы президент.\n'
        f'🏙 Столица: <code>{sector}</code> (1 солдат)',
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
        await message.answer('❌ У вас нет героя.'); return
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
        f'.двигаться R1-S5 — переместиться\n'
        f'.атаковать R1-S5 — атаковать',
        parse_mode=ParseMode.HTML)

@dp.message(F.text.regexp(r'\.двигаться\s+(R\d+-S\d+)'))
async def move_hero(message: Message):
    sector = message.text.split()[1].upper()
    user_id = str(message.from_user.id)
    hero = get_hero(user_id)
    if not hero or not hero["alive"]:
        await message.answer('❌ Герой недоступен.'); return
    valid = [f"R{r+1}-S{s+1}" for r in range(RINGS) for s in range(SECTORS)]
    if sector not in valid:
        await message.answer('❌ Неверный сектор.'); return
    cid = get_user_country(user_id)
    owner = db["sectors"].get(sector, {}).get("owner")
    if owner and owner != cid:
        await message.answer(f'❌ Сектор <code>{sector}</code> чужой.', parse_mode=ParseMode.HTML); return
    hero["sector"] = sector
    save_db(db)
    await message.answer(f'🚶 Герой в <code>{sector}</code>.', parse_mode=ParseMode.HTML)

@dp.message(F.text.regexp(r'\.атаковать\s+(R\d+-S\d+)$'))
async def hero_attack(message: Message):
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

# ===== ПЕРЕМЕЩЕНИЕ ВОЙСК =====
@dp.message(F.text.regexp(r'\.переместить\s+(R\d+-S\d+)\s+(R\d+-S\d+)\s+(\d+)'))
async def move_troops(message: Message):
    parts = message.text.split()
    src = parts[1].upper()
    dst = parts[2].upper()
    amount = int(parts[3])
    user_id = str(message.from_user.id)
    cid = get_user_country(user_id)
    if not cid or not is_president(user_id, cid):
        await message.answer('❌ Только президент.'); return
    if amount <= 0:
        await message.answer('❌ Минимум 1.'); return
    
    # Проверка src/dst
    if src not in [s for s in db["sectors"] if db["sectors"][s].get("owner") == cid]:
        await message.answer(f'❌ <code>{src}</code> не ваш.', parse_mode=ParseMode.HTML); return
    if dst not in [s for s in db["sectors"] if db["sectors"][s].get("owner") == cid]:
        await message.answer(f'❌ <code>{dst}</code> не ваш.', parse_mode=ParseMode.HTML); return
    if src == dst:
        await message.answer('❌ Сектора совпадают.'); return
    
    # Проверка радиуса
    reachable = bfs_within_radius(src, MOVE_RADIUS)
    if dst not in reachable:
        await message.answer(f'❌ <code>{dst}</code> слишком далеко (радиус {MOVE_RADIUS}).', parse_mode=ParseMode.HTML); return
    
    # Проверка наличия войск
    army = db["armies"].get(cid, {}).get(src)
    if not army or army["count"] < amount:
        await message.answer(f'❌ В <code>{src}</code> нет {amount} солдат.', parse_mode=ParseMode.HTML); return
    
    # Перемещение
    army["count"] -= amount
    if army["count"] <= 0:
        del db["armies"][cid][src]
    if dst not in db["armies"][cid]:
        db["armies"][cid][dst] = {"count": 0, "acted": []}
    db["armies"][cid][dst]["count"] += amount
    save_db(db)
    
    await message.answer(
        f'🚚 Перемещено <b>{amount}</b> солдат из <code>{src}</code> в <code>{dst}</code>.',
        parse_mode=ParseMode.HTML)

# ===== АТАКА АРМИЕЙ (только на соседние) =====
@dp.message(F.text.regexp(r'\.атаковать\s+(R\d+-S\d+)\s+(R\d+-S\d+)\s+(\d+)'))
async def army_attack(message: Message):
    parts = message.text.split()
    src = parts[1].upper()
    dst = parts[2].upper()
    amount = int(parts[3])
    user_id = str(message.from_user.id)
    cid = get_user_country(user_id)
    if not cid or not is_president(user_id, cid):
        await message.answer('❌ Только президент.'); return
    
    valid = [f"R{r+1}-S{s+1}" for r in range(RINGS) for s in range(SECTORS)]
    if src not in valid or dst not in valid:
        await message.answer('❌ Неверные сектора.'); return
    if src == dst:
        await message.answer('❌ Сектора совпадают.'); return
    if amount <= 0:
        await message.answer('❌ Минимум 1.'); return
    
    # Проверка соседства
    if not is_neighbor(src, dst):
        await message.answer(f'❌ <code>{dst}</code> не соседний с <code>{src}</code>. Атака только на соседние!', parse_mode=ParseMode.HTML); return
    
    army = db["armies"].get(cid, {}).get(src)
    if not army:
        await message.answer(f'❌ В <code>{src}</code> нет ваших войск.', parse_mode=ParseMode.HTML); return
    
    available = army["count"] - len(army["acted"])
    if amount > available:
        await message.answer(f'❌ В <code>{src}</code> доступно {available} солдат.', parse_mode=ParseMode.HTML); return
    
    army["count"] -= amount
    if army["count"] <= 0:
        del db["armies"][cid][src]
    for _ in range(amount):
        army["acted"].append(f"s_{int(time.time())}_{random.randint(1000,9999)}")
    
    pending = db["pending_attacks"].setdefault(dst, [])
    pending.append({"cid": cid, "amount": amount, "from": src, "timestamp": int(time.time())})
    save_db(db)
    
    await message.answer(
        f'⚔️ <b>{amount}</b> солдат из <code>{src}</code> атакуют <code>{dst}</code>!',
        parse_mode=ParseMode.HTML)
    other = [a for a in pending if a["cid"] != cid]
    if other:
        await resolve_pending_attacks(dst, message)

# ===== РАЗРЕШЕНИЕ =====
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
            if cid != winner_cid and cid in db["armies"] and sector in db["armies"][cid]:
                del db["armies"][cid][sector]
        if winner_cid not in db["armies"]: db["armies"][winner_cid] = {}
        if sector not in db["armies"][winner_cid]:
            db["armies"][winner_cid][sector] = {"count": 0, "acted": []}
        db["armies"][winner_cid][sector]["count"] += winner_power
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
            f'🎲 {roll} (нужно {threshold})\n'
            f'✅ Сектор захвачен!',
            parse_mode=ParseMode.HTML)
        await ask_sector_role(sector, winner_cid, message)
    else:
        for cid in forces:
            if cid in db["armies"] and sector in db["armies"][cid]:
                del db["armies"][cid][sector]
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
            f'🎲 {roll} (нужно {threshold})\n'
            f'❌ Победитель проиграл! Все силы уничтожены.',
            parse_mode=ParseMode.HTML)

async def resolve_single_attack(sector, cid, message: Message):
    pending = db["pending_attacks"].get(sector, [])
    total_force = sum(a.get("amount", 0) + a.get("power", 0) for a in pending)
    owner = db["sectors"].get(sector, {}).get("owner")
    is_capital = db["countries"].get(owner, {}).get("capital") == sector if owner else False
    if not owner:
        db["sectors"][sector] = {"owner": cid, "status": "captured"}
        if cid not in db["armies"]: db["armies"][cid] = {}
        if sector not in db["armies"][cid]:
            db["armies"][cid][sector] = {"count": 0, "acted": []}
        db["armies"][cid][sector]["count"] += total_force
        db["pending_attacks"][sector] = []
        save_db(db)
        await message.answer(f'🚩 <code>{sector}</code> присоединён.', parse_mode=ParseMode.HTML)
        await ask_sector_role(sector, cid, message)
        return
    enemy_army = db["armies"].get(owner, {}).get(sector, {}).get("count", 0)
    enemy_heroes_count = len([1 for uid, h in db["heroes"].items() if h.get("sector") == sector and h.get("alive") and db["users"].get(uid) == owner])
    total_enemy = enemy_army + enemy_heroes_count * HERO_POWER
    if total_enemy == 0:
        db["sectors"][sector] = {"owner": cid, "status": "captured"}
        if owner in db["armies"] and sector in db["armies"][owner]:
            del db["armies"][owner][sector]
        if cid not in db["armies"]: db["armies"][cid] = {}
        if sector not in db["armies"][cid]:
            db["armies"][cid][sector] = {"count": 0, "acted": []}
        db["armies"][cid][sector]["count"] += total_force
        extra = ""
        if is_capital:
            extra = f'\n💀 <b>СТОЛИЦА ПАЛА!</b> {db["countries"][owner]["name"]} уничтожена!'
            del db["countries"][owner]
            if owner in db["armies"]: del db["armies"][owner]
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
        if owner in db["armies"] and sector in db["armies"][owner]:
            del db["armies"][owner][sector]
        for uid, h in list(db["heroes"].items()):
            if h.get("sector") == sector and h.get("alive") and db["users"].get(uid) == owner:
                h["alive"] = False
                h["respawn_at"] = int(time.time()) + RESPAWN_TIME
        if cid not in db["armies"]: db["armies"][cid] = {}
        if sector not in db["armies"][cid]:
            db["armies"][cid][sector] = {"count": 0, "acted": []}
        db["armies"][cid][sector]["count"] += total_force
        extra = ""
        if is_capital:
            extra = f'\n💀 <b>СТОЛИЦА ПАЛА!</b> {db["countries"][owner]["name"]} уничтожена!'
            del db["countries"][owner]
            if owner in db["armies"]: del db["armies"][owner]
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
    if cid not in db["armies"]: db["armies"][cid] = {}
    if capital not in db["armies"][cid]:
        db["armies"][cid][capital] = {"count": 0, "acted": []}
    db["armies"][cid][capital]["count"] += pairs
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
    garrison_text = ""
    if cid in db["armies"] and db["armies"][cid]:
        for sector, data in db["armies"][cid].items():
            acted = len(data.get("acted", []))
            available = data["count"] - acted
            garrison_text += f'\n  • <code>{sector}</code>: {data["count"]} (готовы: {available})'
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

# ===== ТАБЛИЦА ЛИДЕРОВ =====
@dp.message(F.text == "🏆 Лидеры")
async def btn_leaders(message: Message):
    countries_list = []
    for cid, c in db["countries"].items():
        territories = len([s for s, info in db["sectors"].items() if info.get("owner") == cid])
        total_army = sum(
            data.get("count", 0) for s, data in db["armies"].get(cid, {}).items()
        )
        heroes_alive = len([1 for uid, h in db["heroes"].items() if h.get("alive") and db["users"].get(uid) == cid])
        countries_list.append({
            "cid": cid,
            "name": c["name"],
            "color": c["color"],
            "territories": territories,
            "army": total_army,
            "heroes": heroes_alive,
            "members": len(c["members"])
        })
    
    if not countries_list:
        await message.answer('📊 Пока нет стран для рейтинга.')
        return
    
    # Сортировка по территориям (по убыванию), затем по армии
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
        "<b>🌍 Страна:</b>\n"
        "• Кнопка «Создать фракцию»\n"
        "• Кнопка «Присоединиться»\n\n"
        "<b>🦸 Герой:</b>\n"
        "<code>.двигаться R1-S5</code> — переместиться\n"
        "<code>.атаковать R1-S5</code> — атаковать (только соседний)\n\n"
        "<b>👑 Президент:</b>\n"
        "• Кнопка «Произвести» — в столицу\n"
        "<code>.переместить R1-S5 R1-S6 3</code> — перебросить 3 солдата (радиус 2, только своя территория)\n"
        "<code>.атаковать R1-S5 R2-S3 2</code> — атаковать соседний сектор 2 солдатами\n"
        "<code>.назначить_мир R1-S5 кузня</code>\n\n"
        "<b>🏆 Лидеры:</b>\n"
        "• Кнопка «Лидеры» — рейтинг стран\n\n"
        "⚔️ <b>Правила:</b>\n"
        "• Атака — только на соседний сектор\n"
        "• Перемещение — радиус 2 по своей территории\n"
        "• 1 ход на солдата в день\n"
        "• Встречный бой при атаке 2 стран на 1 сектор\n\n"
        "📐 Формат секторов: <code>R1-S1</code> (R 1-8, S 1-24)",
        parse_mode=ParseMode.HTML)

# ===== НАЗНАЧИТЬ МИР =====
@dp.message(F.text.regexp(r'\.назначить_мир\s+(R\d+-S\d+)\s+(кузня|людской)'))
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
        await message.answer('❌ Столица уже людской мир.'); return
    db["worlds"][sector] = {"type": world_type, "country": cid}
    save_db(db)
    await message.answer(f'🏭 <code>{sector}</code> — <b>{world_type}</b>.', parse_mode=ParseMode.HTML)

# ===== ЗАПУСК =====
async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())