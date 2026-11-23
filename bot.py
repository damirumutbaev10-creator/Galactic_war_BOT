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
UNIQUE_POWER = 5
UNIQUE_USES = 3
RESPAWN_TIME = 3600
MOVE_RADIUS = 2

# ===== 4 ФРАКЦИИ =====
FACTIONS = {
    "super_earth": {
        "name": "Супер-Земля",
        "color": "#2E5C8A",
        "capital": "R0-S1",
        "unique_unit": None,
        "unique_power": 0,
        "production_per_sector": 1.0
    },
    "automatons": {
        "name": "Автоматоны",
        "color": "#8B0000",
        "capital": "R4-S7",
        "unique_unit": "Киберстан",
        "unique_power": UNIQUE_POWER,
        "production_per_sector": 0.5
    },
    "terminids": {
        "name": "Терминиды",
        "color": "#C68E17",
        "capital": "R4-S19",
        "unique_unit": "Тираниды",
        "unique_power": UNIQUE_POWER,
        "production_per_sector": 0.5
    },
    "illuminate": {
        "name": "Иллюминаты",
        "color": "#7B68EE",
        "capital": "R7-S12",
        "unique_unit": "Иллюминаты",
        "unique_power": UNIQUE_POWER,
        "production_per_sector": 0.5
    }
}

FACTION_TERRITORIES = {
    "super_earth": ["R0-S1", "R0-S2", "R1-S1", "R1-S2", "R1-S3", "R2-S1", "R2-S2"],
    "automatons": ["R4-S7", "R4-S8", "R4-S9", "R5-S7", "R5-S8", "R3-S7", "R3-S8"],
    "terminids": ["R4-S19", "R4-S20", "R4-S21", "R5-S19", "R5-S20", "R3-S19", "R3-S20"],
    "illuminate": ["R7-S12", "R7-S13", "R7-S14", "R8-S12", "R8-S13", "R6-S12", "R6-S13"]
}

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ===== СОСТОЯНИЯ =====
class CreatePresident(StatesGroup):
    faction = State()
    name = State()

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
        "factions": {
            "super_earth": {"president": None, "members": [], "soldiers": {}, "heroes": {}},
            "automatons": {"president": None, "members": [], "soldiers": {}, "heroes": {}},
            "terminids": {"president": None, "members": [], "soldiers": {}, "heroes": {}},
            "illuminate": {"president": None, "members": [], "soldiers": {}, "heroes": {}}
        },
        "sectors": {},
        "worlds": {},
        "pending_attacks": {},
        "last_day_reset": ""
    }

def save_db(db):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

db = load_db()

# ===== ИНИЦИАЛИЗАЦИЯ =====
def init_factions():
    for fid, faction in FACTIONS.items():
        if fid not in db["factions"]:
            db["factions"][fid] = {"president": None, "members": [], "soldiers": {}, "heroes": {}}
        for sector in FACTION_TERRITORIES[fid]:
            db["sectors"][sector] = {"owner": fid, "status": "captured"}
            if sector == faction["capital"]:
                db["worlds"][sector] = {"type": "людской", "country": fid}
            elif sector not in db["worlds"]:
                db["worlds"][sector] = {"type": "кузня" if random.random() < 0.5 else "людской", "country": fid}
    save_db(db)

init_factions()

# ===== УТИЛИТЫ =====
def current_day_id():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def get_user_faction(user_id):
    for fid, f in db["factions"].items():
        if str(user_id) in f["members"]:
            return fid
    return None

def is_president(user_id, fid):
    return db["factions"][fid]["president"] == str(user_id)

def get_hero(user_id):
    for fid, f in db["factions"].items():
        if str(user_id) in f["heroes"]:
            return f["heroes"][str(user_id)]
    return None

def main_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🌍 Выбрать фракцию"), KeyboardButton(text="👑 Стать президентом")],
            [KeyboardButton(text="🏛 Моя фракция"), KeyboardButton(text="🗺 Карта")],
            [KeyboardButton(text="⚔️ Атаковать"), KeyboardButton(text="🚚 Переместить")],
            [KeyboardButton(text="🏭 Произвести"), KeyboardButton(text="📊 Гарнизоны")],
            [KeyboardButton(text="🦸 Мой герой"), KeyboardButton(text="🏆 Лидеры")],
            [KeyboardButton(text="📖 Помощь")],
        ],
        resize_keyboard=True
    )

def reset_actions():
    day = current_day_id()
    if db.get("last_day_reset") == day:
        return
    for fid in db["factions"]:
        for sid, s in db["factions"][fid]["soldiers"].items():
            s["acted"] = ""
            if s.get("unique", False):
                s["uses_today"] = 0
    db["last_day_reset"] = day
    save_db(db)

def next_soldier_id(fid):
    existing = set(db["factions"][fid]["soldiers"].keys())
    i = 1
    while f"s{i}" in existing:
        i += 1
    return f"s{i}"

def next_unique_id(fid):
    existing = set(db["factions"][fid]["soldiers"].keys())
    i = 1
    while f"u{i}" in existing:
        i += 1
    return f"u{i}"

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

# ===== ХЕЛПЕРЫ СОЛДАТ =====
def get_soldiers_in_sector(fid, sector):
    res = []
    for sid, s in db["factions"][fid]["soldiers"].items():
        if s["sector"] == sector:
            res.append((sid, s))
    return res

def count_available_in_sector(fid, sector):
    day = current_day_id()
    count = 0
    for sid, s in get_soldiers_in_sector(fid, sector):
        if s.get("unique", False):
            if s.get("uses_today", 0) < UNIQUE_USES:
                count += 1
        elif s["acted"] != day:
            count += 1
    return count

def count_total_in_sector(fid, sector):
    return len(get_soldiers_in_sector(fid, sector))

def take_soldiers(fid, sector, amount):
    day = current_day_id()
    taken = []
    for sid, s in get_soldiers_in_sector(fid, sector):
        if len(taken) >= amount:
            break
        if s.get("unique", False):
            if s.get("uses_today", 0) < UNIQUE_USES:
                s["uses_today"] = s.get("uses_today", 0) + 1
                taken.append(sid)
        else:
            if s["acted"] != day:
                s["acted"] = day
                taken.append(sid)
    return taken

# ===== КАРТА =====
def draw_map():
    fig, ax = plt.subplots(figsize=(16, 16), subplot_kw={'aspect': 'equal'})
    ax.set_xlim(-1.15, 1.15)
    ax.set_ylim(-1.15, 1.15)
    ax.axis('off')
    center_radius = 1 / RINGS
    for s in range(CENTER_SECTORS):
        theta1 = s * (360 / CENTER_SECTORS)
        theta2 = (s + 1) * (360 / CENTER_SECTORS)
        sid = sector_id(0, s)
        info = db["sectors"].get(sid)
        color = "#F0F0F0"
        if info and info.get("owner"):
            color = FACTIONS[info["owner"]]["color"]
        wedge = Wedge((0, 0), center_radius, theta1, theta2, width=center_radius,
                      facecolor=color, edgecolor='gray', linewidth=0.6)
        ax.add_patch(wedge)
        mid_theta = (theta1 + theta2) / 2
        mid_r = center_radius / 2
        x0 = mid_r * math.cos(math.radians(mid_theta))
        y0 = mid_r * math.sin(math.radians(mid_theta))
        tx = -math.sin(math.radians(mid_theta))
        ty = math.cos(math.radians(mid_theta))
        ax.text(x0, y0, sid, ha='center', va='center', fontsize=4,
                rotation=mid_theta, rotation_mode='anchor', color='black')
        total_army = sum(len([1 for sdata in db["factions"][fid]["soldiers"].values() if sdata["sector"] == sid]) for fid in db["factions"])
        if total_army > 0:
            ax.plot(x0 - 0.015 * tx, y0 - 0.015 * ty, 'ko', markersize=4, zorder=8)
        if info and info.get("owner"):
            if sid == FACTIONS[info["owner"]]["capital"]:
                ax.plot(x0, y0, marker='*', color='gold', markeredgecolor='black', markersize=16, zorder=10)
        heroes_here = [h for fid in db["factions"] for h in db["factions"][fid]["heroes"].values() if h.get("sector") == sid and h.get("alive")]
        if heroes_here:
            ax.plot(x0 + 0.015 * tx, y0 + 0.015 * ty, marker='D', color='lime', markeredgecolor='black', markersize=7, zorder=9)
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
                color = FACTIONS[info["owner"]]["color"]
            wedge = Wedge((0, 0), r_outer, theta1, theta2, width=r_outer - r_inner,
                          facecolor=color, edgecolor='gray', linewidth=0.6)
            ax.add_patch(wedge)
            mid_theta = (theta1 + theta2) / 2
            mid_r = (r_outer + r_inner) / 2
            x0 = mid_r * math.cos(math.radians(mid_theta))
            y0 = mid_r * math.sin(math.radians(mid_theta))
            tx = -math.sin(math.radians(mid_theta))
            ty = math.cos(math.radians(mid_theta))
            ax.text(x0, y0, sid, ha='center', va='center', fontsize=3.5,
                    rotation=mid_theta, rotation_mode='anchor', color='black')
            total_army = sum(len([1 for sdata in db["factions"][fid]["soldiers"].values() if sdata["sector"] == sid]) for fid in db["factions"])
            if total_army > 0:
                ax.plot(x0 - 0.025 * tx, y0 - 0.025 * ty, 'ko', markersize=4, zorder=8)
            if info and info.get("owner"):
                if sid == FACTIONS[info["owner"]]["capital"]:
                    ax.plot(x0, y0, marker='*', color='gold', markeredgecolor='black', markersize=18, zorder=10)
            heroes_here = [h for fid in db["factions"] for h in db["factions"][fid]["heroes"].values() if h.get("sector") == sid and h.get("alive")]
            if heroes_here:
                ax.plot(x0 + 0.025 * tx, y0 + 0.025 * ty, marker='D', color='lime', markeredgecolor='black', markersize=8, zorder=9)
    plt.savefig('map.png', dpi=130, bbox_inches='tight', transparent=True)
    plt.close()
    return 'map.png'

# ===== СТАРТ =====
@dp.message(Command("start"))
async def cmd_start(message: Message):
    reset_actions()
    user_id = str(message.from_user.id)
    fid = get_user_faction(user_id)
    if fid:
        role = "👑 Президент" if is_president(user_id, fid) else "🦸 Герой"
        await message.answer(
            f'👋 С возвращением, <b>{message.from_user.first_name}</b>!\n\n'
            f'Вы в фракции <b>{FACTIONS[fid]["name"]}</b>.\n'
            f'Ваша роль: {role}',
            reply_markup=main_menu_kb(),
            parse_mode=ParseMode.HTML
        )
    else:
        await message.answer(
            f'👋 Привет, <b>{message.from_user.first_name}</b>!\n\n'
            f'🎮 Игра по захвату территорий (Helldivers 2).\n'
            f'Выбери действие:',
            reply_markup=main_menu_kb(),
            parse_mode=ParseMode.HTML
        )

# ===== ВЫБОР ФРАКЦИИ =====
@dp.message(F.text == "🌍 Выбрать фракцию")
async def btn_choose_faction(message: Message):
    if get_user_faction(message.from_user.id):
        await message.answer('❌ Вы уже в фракции!')
        return
    buttons = []
    for fid, faction in FACTIONS.items():
        pres = db["factions"][fid]["president"]
        pres_name = f"👑 {pres}" if pres else "❌ нет"
        buttons.append([InlineKeyboardButton(text=f'{faction["name"]} ({pres_name})', callback_data=f"join_{fid}")])
    await message.answer('🌍 <b>Выберите фракцию:</b>', reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith('join_'))
async def join_faction(callback: CallbackQuery):
    fid = callback.data.split('_')[1]
    user_id = str(callback.from_user.id)
    if get_user_faction(user_id):
        await callback.answer('Вы уже в фракции!', show_alert=True); return
    if fid not in FACTIONS:
        await callback.answer('Не найдено', show_alert=True); return
    db["factions"][fid]["members"].append(user_id)
    capital = FACTIONS[fid]["capital"]
    db["factions"][fid]["heroes"][user_id] = {"sector": capital, "alive": True, "respawn_at": 0}
    save_db(db)
    await callback.message.edit_text(f'✅ Вы присоединились к <b>{FACTIONS[fid]["name"]}</b>!\n🦸 Вы герой в столице <code>{capital}</code>.', parse_mode=ParseMode.HTML)
    await callback.answer()

# ===== ПРЕЗИДЕНТ =====
@dp.message(F.text == "👑 Стать президентом")
async def btn_become_president(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)
    if get_user_faction(user_id):
        await message.answer('❌ Вы уже в фракции!'); return
    free = [fid for fid, f in db["factions"].items() if not f["president"]]
    if not free:
        await message.answer('❌ Все 4 фракции заняты.'); return
    buttons = [[InlineKeyboardButton(text=FACTIONS[fid]["name"], callback_data=f"pres_{fid}")] for fid in free]
    await message.answer('👑 <b>Выберите фракцию:</b>', reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith('pres_'))
async def pres_faction_chosen(callback: CallbackQuery, state: FSMContext):
    fid = callback.data.split('_')[1]
    if db["factions"][fid]["president"]:
        await callback.answer('Уже есть президент!', show_alert=True); return
    await state.update_data(fid=fid)
    await state.set_state(CreatePresident.name)
    await callback.message.edit_text(f'👑 Вы станете президентом <b>{FACTIONS[fid]["name"]}</b>.\n\nВведите имя (2–30):', parse_mode=ParseMode.HTML)
    await callback.answer()

@dp.message(CreatePresident.name)
async def pres_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 2 or len(name) > 30:
        await message.answer('❌ 2–30 символов:'); return
    data = await state.get_data()
    fid = data["fid"]
    user_id = str(message.from_user.id)
    db["factions"][fid]["president"] = user_id
    db["factions"][fid]["members"].append(user_id)
    capital = FACTIONS[fid]["capital"]
    for _ in range(3):
        sid = next_soldier_id(fid)
        db["factions"][fid]["soldiers"][sid] = {"sector": capital, "acted": ""}
    save_db(db)
    await state.clear()
    await message.answer(
        f'👑 Вы стали президентом <b>{FACTIONS[fid]["name"]}</b>!\n'
        f'🏙 Столица: <code>{capital}</code>\n⚔️ 3 солдата в столице.\n'
        f'⚠️ Как президент, вы не имеете героя.',
        reply_markup=main_menu_kb(), parse_mode=ParseMode.HTML)

# ===== МОЯ ФРАКЦИЯ =====
@dp.message(F.text == "🏛 Моя фракция")
async def btn_my_faction(message: Message):
    user_id = str(message.from_user.id)
    fid = get_user_faction(user_id)
    if not fid:
        await message.answer('❌ Вы не в фракции.'); return
    f = db["factions"][fid]
    faction = FACTIONS[fid]
    role = "👑 Президент" if is_president(user_id, fid) else "🦸 Герой"
    territories = [s for s, info in db["sectors"].items() if info.get("owner") == fid]
    kuzni = [s for s, w in db["worlds"].items() if w.get("country") == fid and w["type"] == "кузня"]
    lyudi = [s for s, w in db["worlds"].items() if w.get("country") == fid and w["type"] == "людской"]
    day = current_day_id()
    total_soldiers = len(f["soldiers"])
    ready = sum(1 for s in f["soldiers"].values() if s.get("unique", False) or s["acted"] != day)
    heroes_alive = len([h for h in f["heroes"].values() if h.get("alive")])
    unique_info = f'\n🦾 Уникальный юнит: <b>{faction["unique_unit"]}</b> (сила {faction["unique_power"]}, 3 атаки/день)' if faction["unique_unit"] else ""
    await message.answer(
        f'🏛 <b>{faction["name"]}</b>\n'
        f'👑 Президент: <code>{f["president"] or "нет"}</code>\n'
        f'👥 Участников: {len(f["members"])}\n'
        f'🗺 Территорий: {len(territories)}\n'
        f'🏭 Кузни: {len(kuzni)} | Людские: {len(lyudi)}\n'
        f'🏙 Столица: <code>{faction["capital"]}</code>\n'
        f'⚔️ Солдат: {total_soldiers} (готовы: {ready})\n'
        f'🦸 Героев: {heroes_alive}{unique_info}\n'
        f'🎖 Ваша роль: {role}',
        parse_mode=ParseMode.HTML)

# ===== ГЕРОЙ =====
@dp.message(F.text == "🦸 Мой герой")
async def btn_hero(message: Message):
    user_id = str(message.from_user.id)
    hero = get_hero(user_id)
    if not hero:
        await message.answer('👑 Вы президент — у вас нет героя.'); return
    fid = get_user_faction(user_id)
    if not hero["alive"]:
        remaining = max(0, hero.get("respawn_at", 0) - int(time.time()))
        await message.answer(f'💀 <b>Герой мёртв.</b>\n⏳ Воскрешение: {remaining // 60} мин {remaining % 60} сек.', parse_mode=ParseMode.HTML); return
    await message.answer(
        f'🦸 <b>Герой</b>\n📛 {message.from_user.first_name}\n🏛 {FACTIONS[fid]["name"]}\n'
        f'📍 Сектор: <code>{hero["sector"]}</code>\n💪 Сила: {HERO_POWER}\n\n'
        f'<code>.двигаться R1-S5</code>\n<code>.атаковать R1-S5</code>',
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
    fid = get_user_faction(user_id)
    owner = db["sectors"].get(sector, {}).get("owner")
    if owner and owner != fid:
        await message.answer(f'❌ Сектор чужой.', parse_mode=ParseMode.HTML); return
    hero["sector"] = sector
    save_db(db)
    await message.answer(f'🚶 Герой в <code>{sector}</code>.', parse_mode=ParseMode.HTML)

@dp.message(F.text.regexp(r'^\.атаковать\s+(R\d+-S\d+)$'))
async def hero_attack_cmd(message: Message):
    sector = message.text.split()[1].upper()
    user_id = str(message.from_user.id)
    hero = get_hero(user_id)
    if not hero or not hero["alive"]:
        await message.answer('❌ Герой недоступен.'); return
    fid = get_user_faction(user_id)
    owner = db["sectors"].get(sector, {}).get("owner")
    if owner == fid:
        await message.answer('❌ Это ваш сектор.'); return
    if hero["sector"] != sector:
        await message.answer(f'❌ Герой в <code>{hero["sector"]}</code>.', parse_mode=ParseMode.HTML); return
    pending = db["pending_attacks"].setdefault(sector, [])
    pending.append({"fid": fid, "hero": user_id, "power": HERO_POWER, "timestamp": int(time.time())})
    save_db(db)
    await message.answer(f'⚔️ Атака героя на <code>{sector}</code>.', parse_mode=ParseMode.HTML)
    other = [a for a in pending if a["fid"] != fid]
    if other:
        await resolve_pending_attacks(sector, message)
    else:
        await resolve_single_attack(sector, fid, message)

# ===== АТАКА (кнопка) =====
@dp.message(F.text == "⚔️ Атаковать")
async def btn_attack(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)
    fid = get_user_faction(user_id)
    if not fid:
        await message.answer('❌ Вы не в фракции.'); return
    if not is_president(user_id, fid):
        await message.answer('❌ Только президент.'); return
    sectors_with_available = {}
    for sid, s in db["factions"][fid]["soldiers"].items():
        day = current_day_id()
        if s.get("unique", False):
            if s.get("uses_today", 0) < UNIQUE_USES:
                sectors_with_available[s["sector"]] = sectors_with_available.get(s["sector"], 0) + 1
        elif s["acted"] != day:
            sectors_with_available[s["sector"]] = sectors_with_available.get(s["sector"], 0) + 1
    if not sectors_with_available:
        await message.answer('❌ Все солдаты уже сходили сегодня.'); return
    buttons = []
    for sector, avail in sorted(sectors_with_available.items()):
        total = count_total_in_sector(fid, sector)
        buttons.append([InlineKeyboardButton(text=f"📍 {sector} — {avail} готовы (из {total})", callback_data=f"atk_src_{sector}")])
    await state.set_state(AttackFlow.choose_src)
    await message.answer('⚔️ <b>Атака</b>\n\nВыберите сектор-источник:', reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith("atk_src_"), AttackFlow.choose_src)
async def atk_src_chosen(callback: CallbackQuery, state: FSMContext):
    sector = callback.data.split("_", 2)[2]
    fid = get_user_faction(callback.from_user.id)
    avail = count_available_in_sector(fid, sector)
    await state.update_data(src=sector, max_amount=avail)
    await state.set_state(AttackFlow.choose_amount)
    await callback.message.edit_text(f'📍 Источник: <code>{sector}</code>\nДоступно: <b>{avail}</b>\n\nСколько отправить? (1–{avail})', parse_mode=ParseMode.HTML)
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
    fid = get_user_faction(message.from_user.id)
    buttons = []
    for nb in neighbors(src):
        info = db["sectors"].get(nb, {})
        owner = info.get("owner")
        if owner == fid: continue
        label = f"🏛 {FACTIONS[owner]['name']} ({nb})" if owner else f"🌫 Свободный ({nb})"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"atk_dst_{nb}")])
    if not buttons:
        await message.answer('❌ Нет целей.'); await state.clear(); return
    await message.answer(f'🎯 Отправляем <b>{amount}</b> из <code>{src}</code>.\n\nВыберите цель:', reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith("atk_dst_"), AttackFlow.choose_dst)
async def atk_dst_chosen(callback: CallbackQuery, state: FSMContext):
    dst = callback.data.split("_", 2)[2]
    user_id = str(callback.from_user.id)
    fid = get_user_faction(user_id)
    data = await state.get_data()
    src, amount = data["src"], data["amount"]
    await state.clear()
    avail = count_available_in_sector(fid, src)
    if amount > avail:
        await callback.message.edit_text(f'❌ Доступно только {avail}.', parse_mode=ParseMode.HTML); await callback.answer(); return
    taken = take_soldiers(fid, src, amount)
    if len(taken) < amount:
        await callback.message.edit_text('❌ Ошибка.', parse_mode=ParseMode.HTML); await callback.answer(); return
    for sid in taken:
        del db["factions"][fid]["soldiers"][sid]
    pending = db["pending_attacks"].setdefault(dst, [])
    pending.append({"fid": fid, "amount": len(taken), "from": src, "timestamp": int(time.time())})
    save_db(db)
    await callback.message.edit_text(f'⚔️ <b>{len(taken)}</b> солдат из <code>{src}</code> атакуют <code>{dst}</code>!', parse_mode=ParseMode.HTML)
    await callback.answer()
    other = [a for a in pending if a["fid"] != fid]
    if other:
        await resolve_pending_attacks(dst, callback.message)
    else:
        await resolve_single_attack(dst, fid, callback.message)

# ===== ПЕРЕМЕЩЕНИЕ =====
@dp.message(F.text == "🚚 Переместить")
async def btn_move(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)
    fid = get_user_faction(user_id)
    if not fid or not is_president(user_id, fid):
        await message.answer('❌ Только президент.'); return
    sectors_with_soldiers = set(s["sector"] for s in db["factions"][fid]["soldiers"].values())
    if not sectors_with_soldiers:
        await message.answer('❌ У вас нет войск.'); return
    buttons = [[InlineKeyboardButton(text=f"📍 {sector} — {count_total_in_sector(fid, sector)} солдат", callback_data=f"mv_src_{sector}")] for sector in sorted(sectors_with_soldiers)]
    await state.set_state(MoveFlow.choose_src)
    await message.answer('🚚 <b>Перемещение</b>\n\nВыберите сектор-источник:', reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith("mv_src_"), MoveFlow.choose_src)
async def mv_src_chosen(callback: CallbackQuery, state: FSMContext):
    sector = callback.data.split("_", 2)[2]
    fid = get_user_faction(callback.from_user.id)
    total = count_total_in_sector(fid, sector)
    await state.update_data(src=sector, max_amount=total)
    await state.set_state(MoveFlow.choose_amount)
    await callback.message.edit_text(f'📍 Источник: <code>{sector}</code>\nВсего: <b>{total}</b>\n\nСколько перебросить? (1–{total})', parse_mode=ParseMode.HTML)
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
    fid = get_user_faction(message.from_user.id)
    reachable = bfs_within_radius(src, MOVE_RADIUS)
    my_sectors = [s for s, info in db["sectors"].items() if info.get("owner") == fid]
    buttons = []
    for nb in sorted(reachable):
        if nb in my_sectors and nb != src:
            buttons.append([InlineKeyboardButton(text=f"🏛 {nb} (там {count_total_in_sector(fid, nb)})", callback_data=f"mv_dst_{nb}")])
    if not buttons:
        await message.answer('❌ Нет доступных секторов.'); await state.clear(); return
    await message.answer(f'🎯 Куда перебросить <b>{amount}</b> из <code>{src}</code>?', reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith("mv_dst_"), MoveFlow.choose_dst)
async def mv_dst_chosen(callback: CallbackQuery, state: FSMContext):
    dst = callback.data.split("_", 2)[2]
    user_id = str(callback.from_user.id)
    fid = get_user_faction(user_id)
    data = await state.get_data()
    src, amount = data["src"], data["amount"]
    await state.clear()
    soldiers = get_soldiers_in_sector(fid, src)
    if len(soldiers) < amount:
        await callback.message.edit_text('❌ Недостаточно.', parse_mode=ParseMode.HTML); await callback.answer(); return
    moved = 0
    for sid, s in soldiers:
        if moved >= amount: break
        s["sector"] = dst
        moved += 1
    save_db(db)
    await callback.message.edit_text(f'🚚 Перемещено <b>{moved}</b> солдат: <code>{src}</code> → <code>{dst}</code>.', parse_mode=ParseMode.HTML)
    await callback.answer()

# ===== ГАРНИЗОНЫ =====
@dp.message(F.text == "📊 Гарнизоны")
async def btn_garrisons(message: Message):
    user_id = str(message.from_user.id)
    fid = get_user_faction(user_id)
    if not fid:
        await message.answer('❌ Вы не в фракции.'); return
    day = current_day_id()
    by_sector = {}
    for sid, s in db["factions"][fid]["soldiers"].items():
        sec = s["sector"]
        by_sector.setdefault(sec, {"total": 0, "ready": 0})
        by_sector[sec]["total"] += 1
        if s.get("unique", False):
            if s.get("uses_today", 0) < UNIQUE_USES:
                by_sector[sec]["ready"] += 1
        elif s["acted"] != day:
            by_sector[sec]["ready"] += 1
    if not by_sector:
        await message.answer('📭 Нет солдат.'); return
    lines = ['📊 <b>Гарнизоны</b>\n']
    ta, tr = 0, 0
    for sector, data in sorted(by_sector.items()):
        lines.append(f'📍 <code>{sector}</code>\n   Всего: <b>{data["total"]}</b> | Готовы: <b>{data["ready"]}</b>')
        ta += data["total"]; tr += data["ready"]
    lines.append(f'\n<b>Итого:</b> {ta} солдат, {tr} готовы.')
    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)

# ===== ПРОИЗВОДСТВО =====
@dp.message(F.text == "🏭 Произвести")
async def btn_produce(message: Message):
    user_id = str(message.from_user.id)
    fid = get_user_faction(user_id)
    if not fid:
        await message.answer('❌ Вы не в фракции.'); return
    if not is_president(user_id, fid):
        await message.answer('❌ Только президент.'); return
    kuzni = [s for s, w in db["worlds"].items() if w.get("country") == fid and w["type"] == "кузня"]
    lyudi = [s for s, w in db["worlds"].items() if w.get("country") == fid and w["type"] == "людской"]
    if not kuzni or not lyudi:
        await message.answer('❌ Нужна хотя бы 1 кузня и 1 людской мир.'); return
    production = FACTIONS[fid]["production_per_sector"]
    base = min(len(kuzni), len(lyudi))
    soldiers_count = int(base * production) if fid != "super_earth" else base
    if soldiers_count == 0:
        await message.answer(f'❌ Недостаточно секторов. Нужно больше кузней и людских.'); return
    capital = FACTIONS[fid]["capital"]
    unique_unit = FACTIONS[fid]["unique_unit"]
    produced_normal = soldiers_count
    produced_unique = 0
    if unique_unit and soldiers_count >= 3:
        produced_unique = 1
        produced_normal = soldiers_count - 1
    for _ in range(produced_normal):
        sid = next_soldier_id(fid)
        db["factions"][fid]["soldiers"][sid] = {"sector": capital, "acted": ""}
    for _ in range(produced_unique):
        sid = next_unique_id(fid)
        db["factions"][fid]["soldiers"][sid] = {"sector": capital, "acted": "", "unique": True, "uses_today": 0}
    save_db(db)
    text = f'⚔️ Произведено <b>{produced_normal}</b> солдат'
    if produced_unique:
        text += f' + <b>{produced_unique}</b> {unique_unit}'
    text += f' в столице <code>{capital}</code>!'
    await message.answer(text, parse_mode=ParseMode.HTML)

# ===== КАРТА =====
@dp.message(F.text == "🗺 Карта")
async def btn_map(message: Message):
    await message.answer('🗺 Генерирую карту...')
    path = draw_map()
    await message.answer_photo(FSInputFile(path), caption='Цвет = фракция | ⚫ = войска | ⭐ = столица | 🟢 = герой')

# ===== ЛИДЕРЫ =====
@dp.message(F.text == "🏆 Лидеры")
async def btn_leaders(message: Message):
    factions_list = []
    for fid, faction in FACTIONS.items():
        territories = len([s for s, info in db["sectors"].items() if info.get("owner") == fid])
        total_army = len(db["factions"][fid]["soldiers"])
        heroes_alive = len([h for h in db["factions"][fid]["heroes"].values() if h.get("alive")])
        factions_list.append({"fid": fid, "name": faction["name"], "territories": territories, "army": total_army, "heroes": heroes_alive, "members": len(db["factions"][fid]["members"]), "president": db["factions"][fid]["president"]})
    factions_list.sort(key=lambda x: (x["territories"], x["army"]), reverse=True)
    lines = ["🏆 <b>ДЕМОНСТРАЦИЯ СИЛЫ</b>\n"]
    medals = ["🥇", "🥈", "🥉", "4."]
    for i, c in enumerate(factions_list):
        place = medals[i] if i < 3 else f"{i+1}."
        pres = f"👑 {c['president']}" if c["president"] else "❌ нет"
        lines.append(f'{place} <b>{c["name"]}</b>\n   🗺 Секторов: <b>{c["territories"]}</b> | ⚔️ Армия: <b>{c["army"]}</b>\n   🦸 Героев: {c["heroes"]} | 👥 Участников: {c["members"]}\n   {pres}')
    await message.answer("\n\n".join(lines), parse_mode=ParseMode.HTML)

# ===== ПОМОЩЬ =====
@dp.message(F.text == "📖 Помощь")
async def btn_help(message: Message):
    await message.answer(
        "📖 <b>Команды:</b>\n\n"
        "<b>🌍 Фракция:</b> кнопки выбора\n\n"
        "<b>🦸 Герой:</b>\n<code>.двигаться R1-S5</code>\n<code>.атаковать R1-S5</code>\n\n"
        "<b>👑 Президент:</b>\n• Атаковать\n• Переместить\n• Произвести\n• Гарнизоны\n\n"
        "⚔️ <b>Правила:</b>\n"
        "• Атака — только соседний сектор\n"
        "• Перемещение — радиус 2\n"
        "• 1 ход на солдата, 3 хода на уникального\n"
        "• Встречный бой при 2+ атакующих\n\n"
        "📐 <code>R0-S1</code>…<code>R8-S24</code>",
        parse_mode=ParseMode.HTML)

# ===== РАЗРЕШЕНИЕ АТАК =====
async def resolve_pending_attacks(sector, message: Message):
    pending = db["pending_attacks"].get(sector, [])
    if not pending: return
    forces = {}
    hero_attacks = {}
    for a in pending:
        fid = a["fid"]
        forces[fid] = forces.get(fid, 0) + a.get("amount", 0) + a.get("power", 0)
        if "hero" in a:
            hero_attacks.setdefault(fid, []).append(a["hero"])
    if len(forces) == 1:
        await resolve_single_attack(sector, list(forces.keys())[0], message)
        return
    sorted_forces = sorted(forces.items(), key=lambda x: x[1], reverse=True)
    winner_fid, winner_power = sorted_forces[0]
    second_power = sorted_forces[1][1] if len(sorted_forces) > 1 else 0
    roll = random.randint(1, 20)
    ratio = winner_power / second_power if second_power > 0 else 99
    threshold = 5 if ratio >= 3 else 8 if ratio >= 2 else 10 if ratio >= 1.5 else 11
    winner_name = FACTIONS[winner_fid]["name"]
    if roll >= threshold:
        db["sectors"][sector] = {"owner": winner_fid, "status": "captured"}
        for fid in forces:
            if fid != winner_fid:
                to_remove = [sid for sid, s in db["factions"][fid]["soldiers"].items() if s["sector"] == sector]
                for sid in to_remove:
                    del db["factions"][fid]["soldiers"][sid]
        for _ in range(winner_power):
            sid = next_soldier_id(winner_fid)
            db["factions"][winner_fid]["soldiers"][sid] = {"sector": sector, "acted": current_day_id()}
        for fid, heroes in hero_attacks.items():
            if fid != winner_fid:
                for h_id in heroes:
                    if h_id in db["factions"][fid]["heroes"]:
                        db["factions"][fid]["heroes"][h_id]["alive"] = False
                        db["factions"][fid]["heroes"][h_id]["respawn_at"] = int(time.time()) + RESPAWN_TIME
        db["pending_attacks"][sector] = []
        save_db(db)
        await message.answer(f'⚔️ <b>ВСТРЕЧНЫЙ БОЙ</b> за <code>{sector}</code>!\n🏆 <b>{winner_name}</b> ({winner_power} vs {second_power})\n🎲 {roll} (нужно {threshold})\n✅ Захвачено!', parse_mode=ParseMode.HTML)
    else:
        for fid in forces:
            to_remove = [sid for sid, s in db["factions"][fid]["soldiers"].items() if s["sector"] == sector]
            for sid in to_remove:
                del db["factions"][fid]["soldiers"][sid]
        for heroes in hero_attacks.values():
            for h_id in heroes:
                for fid in db["factions"]:
                    if h_id in db["factions"][fid]["heroes"]:
                        db["factions"][fid]["heroes"][h_id]["alive"] = False
                        db["factions"][fid]["heroes"][h_id]["respawn_at"] = int(time.time()) + RESPAWN_TIME
        db["pending_attacks"][sector] = []
        save_db(db)
        await message.answer(f'⚔️ <b>ВСТРЕЧНЫЙ БОЙ</b> за <code>{sector}</code>!\n🏆 <b>{winner_name}</b> ({winner_power} vs {second_power})\n🎲 {roll} (нужно {threshold})\n❌ Победитель проиграл!', parse_mode=ParseMode.HTML)

async def resolve_single_attack(sector, fid, message: Message):
    pending = db["pending_attacks"].get(sector, [])
    total_force = sum(a.get("amount", 0) + a.get("power", 0) for a in pending)
    owner = db["sectors"].get(sector, {}).get("owner")
    is_capital = FACTIONS.get(owner, {}).get("capital") == sector if owner else False
    if not owner:
        db["sectors"][sector] = {"owner": fid, "status": "captured"}
        for _ in range(total_force):
            sid = next_soldier_id(fid)
            db["factions"][fid]["soldiers"][sid] = {"sector": sector, "acted": current_day_id()}
        db["pending_attacks"][sector] = []
        save_db(db)
        await message.answer(f'🚩 <code>{sector}</code> присоединён.', parse_mode=ParseMode.HTML)
        return
    enemy_soldiers = [sid for sid, s in db["factions"][owner]["soldiers"].items() if s["sector"] == sector]
    enemy_heroes_count = len([1 for h in db["factions"][owner]["heroes"].values() if h.get("sector") == sector and h.get("alive")])
    total_enemy = len(enemy_soldiers) + enemy_heroes_count * HERO_POWER
    if total_enemy == 0:
        db["sectors"][sector] = {"owner": fid, "status": "captured"}
        for _ in range(total_force):
            sid = next_soldier_id(fid)
            db["factions"][fid]["soldiers"][sid] = {"sector": sector, "acted": current_day_id()}
        extra = f'\n💀 <b>СТОЛИЦА ПАЛА!</b> {FACTIONS[owner]["name"]} лишилась столицы!' if is_capital else ""
        db["pending_attacks"][sector] = []
        save_db(db)
        await message.answer(f'🚩 <code>{sector}</code> захвачен без боя!{extra}', parse_mode=ParseMode.HTML)
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
        db["sectors"][sector] = {"owner": fid, "status": "captured"}
        for sid in enemy_soldiers:
            if sid in db["factions"][owner]["soldiers"]:
                del db["factions"][owner]["soldiers"][sid]
        for h_id, h in db["factions"][owner]["heroes"].items():
            if h.get("sector") == sector and h.get("alive"):
                h["alive"] = False
                h["respawn_at"] = int(time.time()) + RESPAWN_TIME
        for _ in range(total_force):
            sid = next_soldier_id(fid)
            db["factions"][fid]["soldiers"][sid] = {"sector": sector, "acted": current_day_id()}
        extra = f'\n💀 <b>СТОЛИЦА ПАЛА!</b> {FACTIONS[owner]["name"]} лишилась столицы!' if is_capital else ""
        db["pending_attacks"][sector] = []
        save_db(db)
        await message.answer(f'🎲 {roll} ({t_str})\n✅ <b>Успех!</b> <code>{sector}</code> захвачен!{extra}', parse_mode=ParseMode.HTML)
    else:
        db["pending_attacks"][sector] = []
        save_db(db)
        await message.answer(f'🎲 {roll} ({t_str})\n❌ <b>Провал!</b> {total_force} солдат погибли.', parse_mode=ParseMode.HTML)

# ===== ЗАПУСК =====
async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
