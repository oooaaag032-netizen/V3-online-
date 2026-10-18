import os
import json
import time
import asyncio
import aiohttp
import re
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, ChatMemberAdministrator, ChatMemberOwner
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

# --- НАСТРОЙКИ ---
BOT_TOKEN = os.getenv('BOT2_TOKEN')
MAIN_ADMIN_ID = 8015996519 
OWNER_USERNAME = "@Vilatma"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DATA_FILE = 'server_data.json'
CACHE_TIME = 60

# --- БАЗА ДАННЫХ ---
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {
        "user_servers": {},
        "admins": [], 
        "managers": [], 
        "banned_users": {}, 
        "stats": {"user_requests": {}}, 
        "allowed_groups": {},
        "playtime": {}, 
    }

def save_data():
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

data = load_data()
cache = {}

# --- ПРОВЕРКИ ---
def is_owner(user_id): return user_id == MAIN_ADMIN_ID
def is_manager(user_id): return is_owner(user_id) or user_id in data.get("managers", [])

def is_banned(user_id):
    uid = str(user_id)
    if uid not in data.get("banned_users", {}): return False
    ban_info = data["banned_users"][uid]
    if ban_info["until"] != 0 and ban_info["until"] < time.time():
        del data["banned_users"][uid]
        save_data()
        return False
    return True

def get_user_servers(user_id):
    uid = str(user_id)
    if uid not in data["user_servers"]: data["user_servers"][uid] = {}
    return data["user_servers"][uid]

def log_stat(user_id):
    uid = str(user_id)
    if "user_requests" not in data["stats"]: data["stats"]["user_requests"] = {}
    data["stats"]["user_requests"][uid] = data["stats"]["user_requests"].get(uid, 0) + 1
    save_data()

# --- API ---
async def get_server_info(ip):
    if ip in cache and time.time() - cache[ip]['time'] < CACHE_TIME:
        return cache[ip]['data']
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.mcstatus.io/v2/status/java/{ip}", timeout=5) as response:
                if response.status == 200:
                    res = await response.json()
                    cache[ip] = {'time': time.time(), 'data': res}
                    return res
    except: pass
    return None

# --- ФОНОВЫЙ ТРЕКЕР ВРЕМЕНИ В ИГРЕ ---
async def track_playtime():
    while True:
        try:
            all_ips = set()
            for uid, servers in data.get("user_servers", {}).items():
                for name, ip in servers.items():
                    all_ips.add(ip)
            
            if not all_ips:
                await asyncio.sleep(60)
                continue

            for ip in all_ips:
                info = await get_server_info(ip)
                if info and info.get("online"):
                    players = [p["name_clean"] for p in info.get("players", {}).get("list", [])]
                    
                    if "playtime" not in data: data["playtime"] = {}
                    if ip not in data["playtime"]: data["playtime"][ip] = {}
                    
                    now = time.time()
                    
                    for player in players:
                        if player not in data["playtime"][ip]:
                            data["playtime"][ip][player] = {"seconds": 0, "last_seen": now}
                        
                        if now - data["playtime"][ip][player]["last_seen"] < 120:
                            data["playtime"][ip][player]["seconds"] += 60
                        
                        data["playtime"][ip][player]["last_seen"] = now
                    
                    for player, p_data in data["playtime"][ip].items():
                        if player not in players:
                            p_data["last_seen"] = 0
                            
            save_data()
        except Exception as e:
            print(f"Ошибка трекера: {e}")
        await asyncio.sleep(60)

# --- КЛАВИАТУРЫ ---
def main_keyboard(uid):
    if is_banned(uid): return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⛔ Вы заблокированы")]], resize_keyboard=True)
    kb = [
        [KeyboardButton(text="📊 Мои серверы"), KeyboardButton(text="➕ Добавить сервер")],
        [KeyboardButton(text="🛡️ Проверить админов")],
        [KeyboardButton(text="📡 Пинг")],
    ]
    if is_manager(uid): kb.append([KeyboardButton(text="⚙️ Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def admin_keyboard(uid):
    kb = [
        [KeyboardButton(text="📊 Статистика юзеров")],
        [KeyboardButton(text="👑 Управляющие ботом"), KeyboardButton(text="🚫 Блокировка юзеров")],
        [KeyboardButton(text="✅ Доступ группам")],
        [KeyboardButton(text="👤 Игровые админы")],
    ]
    if is_owner(uid): kb.append([KeyboardButton(text="📜 Список забаненных")])
    kb.append([KeyboardButton(text="🔙 Выйти в меню")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- СОСТОЯНИЯ ---
class States(StatesGroup):
    add_user_server = State()
    add_manager = State()
    ban_user = State()
    unban_user = State()
    add_group = State()
    add_game_admin = State()
    remove_game_admin = State()

# ==========================================
# --- ГРУППОВАЯ ЗАЩИТА ---
# ==========================================
@dp.my_chat_member()
async def on_bot_added(event: types.ChatMemberUpdated):
    if event.new_chat_member.status == "member" and event.chat.type in ["group", "supergroup"]:
        chat_id = str(event.chat.id)
        now = time.time()
        if chat_id not in data["allowed_groups"] or data["allowed_groups"][chat_id] < now:
            if chat_id not in data["allowed_groups"]:
                data["allowed_groups"][chat_id] = now + (3 * 24 * 60 * 60)
                save_data()
                await bot.send_message(chat_id, f"👋 Привет! У вас есть **3 дня** доступа.\n\nДалее пишите: {OWNER_USERNAME}")
            else:
                await bot.send_message(chat_id, f"⛔ Срок истек. Пишите: {OWNER_USERNAME}")
                await asyncio.sleep(3)
                await bot.leave_chat(chat_id)

# ==========================================
# --- ОБЫЧНЫЕ ПОЛЬЗОВАТЕЛИ ---
# ==========================================
@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    if is_banned(message.from_user.id): return await message.answer("⛔ Вы заблокированы.")
    await message.answer("👋 Привет! Я мультисерверный мониторинг.", reply_markup=main_keyboard(message.from_user.id))

@dp.message(Command("cancel"))
async def cancel_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("✅ Все действия отменены.", reply_markup=main_keyboard(message.from_user.id))

@dp.message(F.text == "🔙 Выйти в меню")
async def back_main(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Главное меню:", reply_markup=main_keyboard(message.from_user.id))

@dp.message(F.text == "📊 Мои серверы")
async def my_servers(message: types.Message, state: FSMContext):
    if is_banned(message.from_user.id): return
    await state.clear()
    servers = get_user_servers(message.from_user.id)
    if not servers: return await message.answer("У вас пока нет серверов. Нажмите '➕ Добавить сервер'.")
    log_stat(message.from_user.id)
    kb = [[InlineKeyboardButton(text=name, callback_data=f"check_{name}")] for name in servers.keys()]
    kb.append([InlineKeyboardButton(text="❌ Удалить сервер", callback_data="remove_server_menu")])
    await message.answer("Выберите сервер для проверки:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("check_"))
async def check_server_callback(call: types.CallbackQuery):
    server_name = call.data.split("_")[1]
    servers = get_user_servers(call.from_user.id)
    if server_name not in servers: return await call.answer("Не найден.", show_alert=True)
    await call.message.edit_text(f"⏳ Проверяю {server_name}...")
    info = await get_server_info(servers[server_name])
    
    if info and info.get("online"):
        online = info.get("players", {}).get("online", 0)
        max_p = info.get("players", {}).get("max", 0)
        names = [p["name_clean"] for p in info.get("players", {}).get("list", [])]
        version = info.get("version", {}).get("name_clean", "Unknown")
        text = f"🟢 <b>{server_name}</b>\n👥 Онлайн: {online}/{max_p}\n⚙️ Версия: {version}\n"
        text += f"👤 Игроки: {', '.join(names[:15])}" if names else "👤 Список скрыт."
    else:
        text = f"🔴 Сервер <b>{server_name}</b> офлайн."
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_servers")]])
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data == "back_to_servers")
async def back_to_servers(call: types.CallbackQuery):
    servers = get_user_servers(call.from_user.id)
    kb = [[InlineKeyboardButton(text=name, callback_data=f"check_{name}")] for name in servers.keys()]
    kb.append([InlineKeyboardButton(text="❌ Удалить сервер", callback_data="remove_server_menu")])
    await call.message.edit_text("Выберите сервер:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.message(F.text == "➕ Добавить сервер")
async def add_user_server_start(message: types.Message, state: FSMContext):
    if is_banned(message.from_user.id): return
    await state.set_state(States.add_user_server)
    await message.answer("Введите <b>Название</b> и <b>IP</b> через пробел:\nПример: <code>МойСервер play.myserver.com</code>\n\n/cancel для отмены", parse_mode="HTML")

@dp.message(States.add_user_server)
async def add_user_server_process(message: types.Message, state: FSMContext):
    await state.clear()
    try:
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2: raise ValueError
        name, ip = parts
        ip = ip.replace("https://", "").replace("http://", "").strip("/")
        uid = str(message.from_user.id)
        if len(data["user_servers"].get(uid, {})) >= 5:
            return await message.answer("❌ У вас максимум 5 серверов.", reply_markup=main_keyboard(message.from_user.id))
        data["user_servers"].setdefault(uid, {})[name] = ip
        save_data()
        await message.answer(f"✅ Сервер <b>{name}</b> добавлен!", reply_markup=main_keyboard(message.from_user.id), parse_mode="HTML")
    except ValueError:
        await message.answer("❌ Ошибка формата. Введите: Название IP", reply_markup=main_keyboard(message.from_user.id))

@dp.callback_query(F.data == "remove_server_menu")
async def remove_server_menu(call: types.CallbackQuery):
    servers = get_user_servers(call.from_user.id)
    if not servers: return await call.answer("Пусто.")
    kb = [[InlineKeyboardButton(text=f"🗑 {name}", callback_data=f"del_{name}")] for name in servers.keys()]
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_servers")])
    await call.message.edit_text("Выберите для удаления:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("del_"))
async def delete_server_callback(call: types.CallbackQuery):
    server_name = call.data.split("_")[1]
    uid = str(call.from_user.id)
    if uid in data["user_servers"] and server_name in data["user_servers"][uid]:
        del data["user_servers"][uid][server_name]
        save_data()
        await call.answer(f"{server_name} удален!")
    await back_to_servers(call)

@dp.message(F.text == "🛡️ Проверить админов")
async def check_admins_global(message: types.Message, state: FSMContext):
    if is_banned(message.from_user.id): return
    await state.clear()
    servers = get_user_servers(message.from_user.id)
    if not servers: return await message.answer("Сначала добавьте серверы.")
    text = "🛡️ <b>Поиск админов:</b>\n"
    found = False
    for name, ip in servers.items():
        info = await get_server_info(ip)
        if info and info.get("online"):
            names = [p["name_clean"] for p in info.get("players", {}).get("list", [])]
            admins_on = [n for n in names if n in data["admins"]]
            if admins_on:
                text += f"🔹 <b>{name}</b>: {', '.join(admins_on)}\n"
                found = True
    if not found: text += "😴 Админов нигде нет."
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "📡 Пинг")
async def ping_menu(message: types.Message, state: FSMContext):
    await state.clear()
    servers = get_user_servers(message.from_user.id)
    if not servers: return await message.answer("Сначала добавьте серверы.")
    kb = [[InlineKeyboardButton(text=f"Пинг {name}", callback_data=f"ping_{name}")] for name in servers.keys()]
    await message.answer("Выберите сервер:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("ping_"))
async def ping_callback(call: types.CallbackQuery):
    name = call.data.split("_")[1]
    ip = get_user_servers(call.from_user.id).get(name)
    if not ip: return await call.answer("Ошибка.")
    await call.message.edit_text(f"⏳ Замеряю...")
    start_time = time.time()
    await get_server_info(ip) 
    ping = int((time.time() - start_time) * 1000)
    await call.message.edit_text(f"📡 Пинг до <b>{name}</b>: ~{ping} ms", parse_mode="HTML")

# ==========================================
# --- КОМАНДЫ ДЛЯ ГРУПП (Время и TPS) ---
# ==========================================
@dp.message(Command("time"))
async def check_playtime(message: types.Message, command: CommandObject):
    if message.chat.type not in ["group", "supergroup"]:
        return await message.answer("⛔ Эта команда работает только в группах!")
    
    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        if not isinstance(member, (ChatMemberAdministrator, ChatMemberOwner)):
            return await message.answer("⛔ Эту команду могут использовать только администраторы группы!")
    except Exception:
        return await message.answer("❌ Не удалось проверить права. Возможно, бот не админ в группе.")

    if not command.args:
        return await message.answer("⚠️ Укажите ник игрока: <code>/time Steve</code>", parse_mode="HTML")
    
    player_nick = command.args.strip()
    found = False
    text = f"⏱ <b>Статистика игрока {player_nick}:</b>\n\n"
    
    if "playtime" not in data:
        return await message.answer("📊 Статистика еще не накопилась.")
        
    for ip, players in data["playtime"].items():
        if player_nick in players:
            seconds = players[player_nick]["seconds"]
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            text += f"🔹 Сервер: <code>{ip}</code>\n   ⏳ Время в игре: {hours} ч. {minutes} мин.\n\n"
            found = True
            
    if not found:
        text = f"❌ Игрок <b>{player_nick}</b> не найден в базе. Бот собирает статистику только когда игроки онлайн на ваших серверах."
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("tps"))
async def check_tps(message: types.Message, command: CommandObject):
    if message.chat.type not in ["group", "supergroup"]:
        return await message.answer("⛔ Эта команда работает только в группах!")
    
    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        if not isinstance(member, (ChatMemberAdministrator, ChatMemberOwner)):
            return await message.answer("⛔ Эту команду могут использовать только администраторы группы!")
    except Exception:
        return await message.answer("❌ Не удалось проверить права. Убедитесь, что бот — админ в группе.")

    if not command.args:
        return await message.answer("⚠️ Укажите IP сервера: <code>/tps play.myserver.com</code>", parse_mode="HTML")
    
    ip = command.args.strip().replace("https://", "").replace("http://", "").strip("/")
    msg = await message.answer(f"⏳ Замеряю TPS и пинг для <code>{ip}</code>...", parse_mode="HTML")

    start_time = time.time()
    info = await get_server_info(ip)
    response_time = int((time.time() - start_time) * 1000)

    if not info or not info.get("online"):
        return await msg.edit_text(f"🔴 Сервер <b>{ip}</b> офлайн или не отвечает.")

    if response_time < 100:
        tps_score, status = 20.0, "🟢 Идеально (20.0 TPS)"
    elif response_time < 300:
        tps_score, status = 19.5, "🟢 Отлично (~19.5 TPS)"
    elif response_time < 600:
        tps_score, status = 18.0, "🟡 Нормально (~18.0 TPS)"
    elif response_time < 1000:
        tps_score, status = 15.0, "🟠 Лаги (~15.0 TPS)"
    else:
        tps_score, status = 10.0, "🔴 Сильные лаги (<10.0 TPS)"

    players = info.get("players", {}).get("online", 0)
    version = info.get("version", {}).get("name_clean", "Unknown")

    text = (f"📊 <b>Анализ сервера:</b> <code>{ip}</code>\n\n"
            f"⚙️ Версия: {version}\n"
            f"👥 Игроков: {players}\n\n"
            f"⏱ <b>Оценка TPS:</b> {status}\n"
            f"📡 Задержка (Ping): {response_time} ms\n\n"
            f"<i>*TPS вычислен на основе задержки API. Для точного TPS нужен RCON.</i>")
    await msg.edit_text(text, parse_mode="HTML")

# ==========================================
# --- АДМИН-ПАНЕЛЬ ---
# ==========================================
@dp.message(F.text == "⚙️ Админ-панель")
async def admin_panel(message: types.Message, state: FSMContext):
    if not is_manager(message.from_user.id): return
    await state.clear()
    await message.answer("⚙️ <b>Админ-панель</b>", reply_markup=admin_keyboard(message.from_user.id), parse_mode="HTML")

@dp.message(F.text == "📊 Статистика юзеров")
async def user_stats(message: types.Message, state: FSMContext):
    if not is_manager(message.from_user.id): return
    await state.clear()
    stats = data["stats"].get("user_requests", {})
    if not stats: return await message.answer("Нет данных.")
    sorted_stats = sorted(stats.items(), key=lambda x: x[1], reverse=True)
    text = "📊 <b>Топ юзеров:</b>\n"
    for uid, count in sorted_stats[:10]:
        try: name = (await bot.get_chat(uid)).first_name
        except: name = "Скрыт"
        text += f"• {name} (<code>{uid}</code>): {count} раз\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "👑 Управляющие ботом")
async def managers_panel(message: types.Message, state: FSMContext):
    if not is_manager(message.from_user.id): return
    await state.clear()
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="➕ Добавить управляющего")], [KeyboardButton(text="🔙 Выйти в меню")]], resize_keyboard=True)
    m_list = "\n".join([f"<code>{m}</code>" for m in data["managers"]]) or "Нет"
    await message.answer(f"👑 <b>Управляющие:</b>\n{m_list}", reply_markup=kb, parse_mode="HTML")

@dp.message(F.text == "➕ Добавить управляющего")
async def add_manager_start(message: types.Message, state: FSMContext):
    if not is_manager(message.from_user.id): return
    await state.set_state(States.add_manager)
    await message.answer("Перешлите сообщение или введите ID:\n/cancel для отмены")

@dp.message(States.add_manager)
async def add_manager_process(message: types.Message, state: FSMContext):
    await state.clear()
    try:
        uid = message.forward_from.id if message.forward_from else int(str(message.text).strip())
        if uid not in data["managers"]:
            data["managers"].append(uid)
            save_data()
            await message.answer(f"✅ {uid} теперь управляющий.", reply_markup=admin_keyboard(message.from_user.id))
        else: await message.answer("Уже есть.", reply_markup=admin_keyboard(message.from_user.id))
    except ValueError:
        await message.answer("❌ Ошибка: нужен ID (цифры).", reply_markup=admin_keyboard(message.from_user.id))

@dp.message(F.text == "🚫 Блокировка юзеров")
async def ban_panel(message: types.Message, state: FSMContext):
    if not is_manager(message.from_user.id): return
    await state.clear()
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⛔ Забанить")], [KeyboardButton(text="✅ Разбанить")], [KeyboardButton(text="🔙 Выйти в меню")]], resize_keyboard=True)
    await message.answer("🚫 Управление:", reply_markup=kb)

@dp.message(F.text == "⛔ Забанить")
async def ban_start(message: types.Message, state: FSMContext):
    if not is_manager(message.from_user.id): return
    await state.set_state(States.ban_user)
    await message.answer("Перешлите сообщение или введите ID для бана:\nФормат: ID Время Причина (Пример: 123456 1д Спам)\n/cancel для отмены")

@dp.message(States.ban_user)
async def ban_process(message: types.Message, state: FSMContext):
    await state.clear()
    try:
        uid = message.forward_from.id if message.forward_from else None
        text_to_parse = str(message.text).strip()
        if uid: parts = text_to_parse.split(maxsplit=1)
        else:
            parts = text_to_parse.split(maxsplit=2)
            uid = int(parts.pop(0))
        if uid == MAIN_ADMIN_ID: return await message.answer("Нельзя забанить владельца!", reply_markup=admin_keyboard(message.from_user.id))
        until = 0
        reason = "Не указана"
        if len(parts) >= 1:
            time_str = parts[0].lower()
            reason = parts[1] if len(parts) > 1 else "Не указана"
            match = re.match(r'(\d+)([чдг])', time_str)
            if match:
                amount, unit = int(match.group(1)), match.group(2)
                seconds = amount * 3600 if unit == 'ч' else amount * 86400 if unit == 'д' else amount * 31536000
                until = time.time() + seconds
            elif time_str != "навсегда":
                return await message.answer("❌ Ошибка формата времени (5ч, 2д, 1г).", reply_markup=admin_keyboard(message.from_user.id))
        data["banned_users"][str(uid)] = {"until": until, "reason": reason}
        save_data()
        time_text = "навсегда" if until == 0 else f"до {datetime.fromtimestamp(until).strftime('%Y-%m-%d %H:%M')}"
        await message.answer(f"✅ {uid} забанен {time_text}.\nПричина: {reason}", reply_markup=admin_keyboard(message.from_user.id))
    except ValueError: await message.answer("❌ Ошибка: ID должен быть числом.", reply_markup=admin_keyboard(message.from_user.id))
    except Exception as e: await message.answer(f"❌ Ошибка: {e}", reply_markup=admin_keyboard(message.from_user.id))

@dp.message(F.text == "✅ Разбанить")
async def unban_start(message: types.Message, state: FSMContext):
    if not is_manager(message.from_user.id): return
    await state.set_state(States.unban_user)
    await message.answer("Введите ID для разбана:\n/cancel для отмены")

@dp.message(States.unban_user)
async def unban_process(message: types.Message, state: FSMContext):
    await state.clear()
    try:
        uid = str(message.text).strip()
        if uid in data["banned_users"]:
            del data["banned_users"][uid]
            save_data()
            await message.answer(f"✅ {uid} разбанен.", reply_markup=admin_keyboard(message.from_user.id))
        else: await message.answer("Нет в бан-листе.", reply_markup=admin_keyboard(message.from_user.id))
    except: await message.answer("❌ Ошибка.", reply_markup=admin_keyboard(message.from_user.id))

@dp.message(F.text == "📜 Список забаненных")
async def view_banned_users(message: types.Message, state: FSMContext):
    if not is_owner(message.from_user.id): return await message.answer("⛔ Нет прав.")
    await state.clear()
    banned = data.get("banned_users", {})
    if not banned: return await message.answer("✅ Список пуст.", reply_markup=admin_keyboard(message.from_user.id))
    text = f"📜 <b>Забаненные ({len(banned)}):</b>\n\n"
    for uid, info in banned.items():
        until, reason = info.get("until", 0), info.get("reason", "Не указана")
        time_text = "Навсегда" if until == 0 else datetime.fromtimestamp(until).strftime('%d.%m %H:%M')
        try: name = (await bot.get_chat(uid)).first_name
        except: name = "Скрыт"
        text += f"• {name} (<code>{uid}</code>)\n  ⏳ {time_text} | 📝 {reason}\n"
    await message.answer(text, reply_markup=admin_keyboard(message.from_user.id), parse_mode="HTML")

@dp.message(F.text == "✅ Доступ группам")
async def group_access_panel(message: types.Message, state: FSMContext):
    if not is_manager(message.from_user.id): return
    await state.clear()
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔓 Выдать доступ")], [KeyboardButton(text="🔙 Выйти в меню")]], resize_keyboard=True)
    now = time.time()
    text = "📋 <b>Группы:</b>\n"
    for gid, expire in data["allowed_groups"].items():
        status = "✅ Активен" if expire > now else "⛔ Истек"
        text += f"ID <code>{gid}</code>: {status} (до {datetime.fromtimestamp(expire).strftime('%Y-%m-%d %H:%M')})\n"
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.message(F.text == "🔓 Выдать доступ")
async def add_group_start(message: types.Message, state: FSMContext):
    if not is_manager(message.from_user.id): return
    await state.set_state(States.add_group)
    await message.answer("Введите ID группы и дни через пробел:\n/cancel для отмены")

@dp.message(States.add_group)
async def add_group_process(message: types.Message, state: FSMContext):
    await state.clear()
    try:
        gid, days = message.text.split()
        expire = time.time() + (int(days) * 24 * 60 * 60)
        data["allowed_groups"][gid] = expire
        save_data()
        await message.answer(f"✅ Группе {gid} выдан доступ на {days} дней.", reply_markup=admin_keyboard(message.from_user.id))
        try: await bot.send_message(gid, f"✅ Доступ продлен на {days} дней!")
        except: pass
    except: await message.answer("❌ Ошибка формата.", reply_markup=admin_keyboard(message.from_user.id))

@dp.message(F.text == "👤 Игровые админы")
async def game_admins_panel(message: types.Message, state: FSMContext):
    if not is_manager(message.from_user.id): return
    await state.clear()
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="➕ Добавить игрового админа")], [KeyboardButton(text="🗑 Удалить игрового админа")], [KeyboardButton(text="🔙 Выйти в меню")]], resize_keyboard=True)
    await message.answer(f"👤 <b>Ники админов:</b>\n{', '.join(data['admins']) or 'Пусто'}", reply_markup=kb, parse_mode="HTML")

@dp.message(F.text == "➕ Добавить игрового админа")
async def add_adm_start(message: types.Message, state: FSMContext):
    if not is_manager(message.from_user.id): return
    await state.set_state(States.add_game_admin)
    await message.answer("Введите игровой ник:\n/cancel для отмены")

@dp.message(States.add_game_admin)
async def add_adm_process(message: types.Message, state: FSMContext):
    await state.clear()
    nick = str(message.text).strip()
    if nick and nick not in data["admins"]:
        data["admins"].append(nick)
        save_data()
        await message.answer(f"✅ {nick} добавлен.", reply_markup=admin_keyboard(message.from_user.id))
    else: await message.answer("Уже есть или пустой ник.", reply_markup=admin_keyboard(message.from_user.id))

@dp.message(F.text == "🗑 Удалить игрового админа")
async def del_adm_start(message: types.Message, state: FSMContext):
    if not is_manager(message.from_user.id): return
    await state.set_state(States.remove_game_admin)
    await message.answer("Введите ник для удаления:\n/cancel для отмены")

@dp.message(States.remove_game_admin)
async def del_adm_process(message: types.Message, state: FSMContext):
    await state.clear()
    nick = str(message.text).strip()
    if nick in data["admins"]:
        data["admins"].remove(nick)
        save_data()
        await message.answer(f"✅ {nick} удален.", reply_markup=admin_keyboard(message.from_user.id))
    else: await message.answer("Нет такого.", reply_markup=admin_keyboard(message.from_user.id))

# --- ЗАПУСК ---
async def main():
    asyncio.create_task(track_playtime())
    print("Бот запущен! Трекер времени активирован.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
