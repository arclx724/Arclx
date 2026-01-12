import asyncio
import logging
import sys
import time
import os
import psutil
import datetime
import re
from typing import Dict, Any, Union

# --- 🛠️ FIX FOR TERMUX DNS ERROR (Sabse Upar) ---
import dns.resolver
dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
dns.resolver.default_resolver.nameservers = ['8.8.8.8']

# --- LIBRARIES ---
from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ChatPermissions
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ContentType

import motor.motor_asyncio  # Async MongoDB
import aiohttp              # Async Requests

# ==========================================
# ⚙️ CONFIGURATION (Yahan apna data daalo)
# ==========================================
API_TOKEN = 'YOUR_BOT_TOKEN_HERE'
MONGO_URL = "YOUR_MONGO_URL_HERE"
OWNER_ID = 1234567890  # Apna Telegram ID (Number)
LOG_CHANNEL_ID = "@c4llli" 
SUPPORT_LINK = "https://t.me/BillieSupport"
UPDATES_LINK = "https://t.me/c4llli"

# --- 📝 LOGGING SETUP ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- 🚀 ASYNC DATABASE & CACHE ---
try:
    client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URL)
    db = client['GroupHelpClone']
    settings_col = db['settings']
    apikeys_col = db['api_keys']
    aikeys_col = db['ai_keys']
    config_col = db['config']
    sudo_col = db['sudoers']
    gban_col = db['gbans']
    logger.info("✅ Async Database Connected!")
except Exception as e:
    logger.critical(f"❌ DB Connection Failed: {e}")
    sys.exit(1)

# ⚡ RAM CACHE SYSTEM
SETTINGS_CACHE: Dict[int, Dict[str, Any]] = {}
SUDO_CACHE = set()
API_KEYS_CACHE = {"nsfw": [], "ai": []}

# --- 🤖 BOT INIT ---
bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher()
START_TIME = time.time()

# --- 🗺️ MAPS ---
CMD_MAP = {
    "nobots": "adding spambots protection",
    "noevents": "join and left filter",
    "nolinks": "links filter",
    "noforwards": "forwards filter",
    "nolocations": "locations filter",
    "nocontacts": "contacts filter",
    "nocommands": "commands filter",
    "nohashtags": "hashtags filter",
    "novoice": "voice filter",
    "profanity": "bad words filter",
    "imagefilter": "unsafe image filter",
    "antiflood": "frequent messages filter",
    "noedit": "edited messages filter",
    "blacklist_active": "bad domains"
}

# ==========================================
# 🛠️ HELPER FUNCTIONS (ASYNC & CACHED)
# ==========================================

async def refresh_caches():
    """Load Sudo & Keys into RAM on startup"""
    global SUDO_CACHE, API_KEYS_CACHE
    # Load Sudo
    SUDO_CACHE.add(OWNER_ID)
    async for s in sudo_col.find({}):
        SUDO_CACHE.add(s['user_id'])
    
    # Load Keys
    async for k in apikeys_col.find({}):
        API_KEYS_CACHE["nsfw"].append(k)
    async for k in aikeys_col.find({}):
        API_KEYS_CACHE["ai"].append(k)
    
    logger.info("🧠 Caches Refreshed (Sudo & APIs)")

async def get_settings(chat_id: int):
    """Get Group Settings from RAM (if avail) or DB"""
    if chat_id in SETTINGS_CACHE:
        return SETTINGS_CACHE[chat_id]
    
    data = await settings_col.find_one({"chat_id": chat_id})
    if not data: data = {}
    SETTINGS_CACHE[chat_id] = data
    return data

async def update_setting(chat_id: int, key: str, value: Any):
    """Update RAM & DB together"""
    # 1. Update DB
    await settings_col.update_one({"chat_id": chat_id}, {"$set": {key: value}}, upsert=True)
    # 2. Update RAM
    if chat_id not in SETTINGS_CACHE:
        SETTINGS_CACHE[chat_id] = {}
    SETTINGS_CACHE[chat_id][key] = value

async def is_sudo(user_id: int) -> bool:
    return user_id in SUDO_CACHE

async def is_admin(chat_id: int, user_id: int) -> bool:
    if await is_sudo(user_id): return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ['administrator', 'creator']
    except:
        return False

async def log_to_channel(text: str):
    """Async Logger"""
    conf = await config_col.find_one({"key": "logger_active"})
    if conf and conf.get('status') is False: return
    try:
        if LOG_CHANNEL_ID: await bot.send_message(LOG_CHANNEL_ID, f"🔔 **LOG:**\n{text}")
    except Exception as e:
        logger.error(f"Logger Error: {e}")

def escape_md(text):
    if not text: return ""
    return text.replace("*", "").replace("_", "").replace("`", "").replace("[", "")

# ==========================================
# 🧠 ASYNC API ENGINES (AIOHTTP)
# ==========================================
async def check_nsfw_async(file_url):
    keys = API_KEYS_CACHE["nsfw"]
    if not keys: return False, "No Keys"
    
    async with aiohttp.ClientSession() as session:
        for key_data in keys:
            try:
                params = {
                    'models': 'nudity',
                    'api_user': key_data['user'],
                    'api_secret': key_data['secret'],
                    'url': file_url
                }
                async with session.get('https://api.sightengine.com/1.0/check.json', params=params) as resp:
                    result = await resp.json()
                    if result['status'] == 'success':
                        nudity = result.get('nudity', {})
                        if (nudity.get('raw', 0) > 0.60) or (nudity.get('partial', 0) > 0.70):
                            return True, "NSFW"
                        return False, "Safe"
            except Exception as e:
                logger.error(f"NSFW API Error: {e}")
                continue
    return False, "Error"

async def get_censored_text_async(text):
    keys = API_KEYS_CACHE["ai"]
    if not keys: return None
    
    system_prompt = "You are a Content Filter. Detect Profanity/Abuse in English/Hindi/Hinglish. Reply 'SAFE' if none. If found, return text with ||badword||."
    
    async with aiohttp.ClientSession() as session:
        for key_data in keys:
            try:
                headers = {"Authorization": f"Bearer {key_data['key']}"}
                payload = {
                    "model": "google/gemini-2.0-flash-001",
                    "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": text}]
                }
                async with session.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        result = data['choices'][0]['message']['content'].strip()
                        if "SAFE" in result: return None
                        return result
            except: continue
    return None

# ==========================================
# 👮‍♂️ ADMIN COMMANDS (BAN/MUTE/AUTH)
# ==========================================
@dp.message(Command("stat", "status"))
async def status_command(message: types.Message):
    if not await is_admin(message.chat.id, message.from_user.id): return

    # Async Permission Check
    try:
        me = await bot.get_chat_member(message.chat.id, bot.id)
        perms = f"{'✅' if me.status=='administrator' else '❌'} Administrator\n{'✅' if me.can_delete_messages else '❌'} Can delete messages\n{'✅' if me.can_restrict_members else '❌'} Can restrict members"
    except: perms = "❓ Permissions Unknown"

    st = await get_settings(message.chat.id)
    filter_list = ""
    order = ["antiflood", "imagefilter", "noevents", "nolinks", "noforwards", "nolocations", "nocontacts", "nocommands", "nohashtags", "novoice", "nobots", "profanity"]
    
    for key in order:
        icon = "✅" if st.get(key) else "▫️"
        filter_list += f"{icon} {CMD_MAP.get(key, key).capitalize()} /{key.split('_')[0]}\n"

    txt = f"**{escape_md((await bot.get_me()).first_name)}**\n{escape_md(message.chat.title)}, **bot status:**\n{perms}\n**Filters:**\n{filter_list}"
    await message.reply(txt)

@dp.message(Command("auth", "unauth", "ban", "unban", "mute", "unmute", "promote", "demote", "authlist", "unauthall"))
async def admin_actions(message: types.Message):
    if not await is_admin(message.chat.id, message.from_user.id): return
    
    cmd = message.text.split()[0].lower()
    chat = message.chat.id

    # Handle Authlist/Unauthall
    if '/authlist' in cmd:
        st = await get_settings(chat)
        if not st.get('auth_users'): return await message.reply("📂 **Auth List is Empty.**")
        return await message.reply(f"🛡️ **Auth Users:** `{st['auth_users']}`")
    
    if '/unauthall' in cmd:
        # Check creator
        try:
            if (await bot.get_chat_member(chat, message.from_user.id)).status == 'creator' or await is_sudo(message.from_user.id):
                await update_setting(chat, "auth_users", [])
                return await message.reply("🗑️ **All Authorized users removed.**")
        except: pass
        return

    # Resolve Target (Async way)
    target = None
    if message.reply_to_message:
        target = message.reply_to_message.from_user
    elif len(message.text.split()) > 1:
        arg = message.text.split()[1]
        if arg.isdigit():
            try: target = await bot.get_chat_member(chat, int(arg))
            except: pass
            if target: target = target.user
        elif arg.startswith("@"):
            try: 
                # aiogram me direct user object nahi milta bina interaction ke, so we warn
                await message.reply("⚠️ Username not supported directly. Please Reply to user or use ID.")
                return
            except: pass

    if not target:
        return await message.reply("❌ **User not found.** Reply or use ID.")

    uid = target.id
    name = escape_md(target.first_name)

    try:
        if '/auth' in cmd:
            await settings_col.update_one({"chat_id": chat}, {"$addToSet": {"auth_users": uid}}, upsert=True)
            if chat in SETTINGS_CACHE: SETTINGS_CACHE[chat].setdefault('auth_users', []).append(uid)
            await message.reply(f"✅ **Authorized:** {name}")

        elif '/unauth' in cmd:
            await settings_col.update_one({"chat_id": chat}, {"$pull": {"auth_users": uid}})
            if chat in SETTINGS_CACHE and 'auth_users' in SETTINGS_CACHE[chat]: 
                if uid in SETTINGS_CACHE[chat]['auth_users']: SETTINGS_CACHE[chat]['auth_users'].remove(uid)
            await message.reply(f"🚫 **Un-Authorized:** {name}")

        elif '/ban' in cmd:
            await bot.ban_chat_member(chat, uid)
            await message.reply(f"🚫 **Banned:** {name}")

        elif '/unban' in cmd:
            await bot.unban_chat_member(chat, uid, only_if_banned=True)
            await message.reply(f"✅ **Unbanned:** {name}")

        elif '/mute' in cmd:
            await bot.restrict_chat_member(chat, uid, permissions=ChatPermissions(can_send_messages=False))
            await message.reply(f"🔇 **Muted:** {name}")

        elif '/unmute' in cmd:
            await bot.restrict_chat_member(chat, uid, permissions=ChatPermissions(can_send_messages=True, can_send_media_messages=True, can_send_other_messages=True))
            await message.reply(f"🔊 **Unmuted:** {name}")
            
        elif '/promote' in cmd:
            await bot.promote_chat_member(chat, uid, can_invite_users=True, can_delete_messages=True, can_restrict_members=True)
            await message.reply(f"✅ **Promoted:** {name}")
            
        elif '/demote' in cmd:
            await bot.promote_chat_member(chat, uid, is_anonymous=False)
            await message.reply(f"⬇️ **Demoted:** {name}")

    except Exception as e:
        await message.reply(f"⚠️ Error: `{e}`")

# --- ⚙️ TOGGLES ---
@dp.message(F.text.startswith("/"))
async def toggle_handler(message: types.Message):
    cmd = message.text.split()[0][1:]
    if cmd not in CMD_MAP: return 
    
    if not await is_admin(message.chat.id, message.from_user.id): return

    args = message.text.split()
    if len(args) < 2:
        return await message.reply(f"Usage: `/{cmd} on` or `off`")

    val = True if args[1].lower() == 'on' else False
    await update_setting(message.chat.id, cmd, val)
    
    user = f"[{escape_md(message.from_user.first_name)}](tg://user?id={message.from_user.id})"
    feat = CMD_MAP.get(cmd, cmd)
    status = "enabled" if val else "disabled"
    icon = "✅" if val else ""
    
    await message.reply(f"{user} | {escape_md(message.chat.title)}, {status} {feat} {icon}.")

# ==========================================
# 🛡️ MAIN FILTERS (The Core Logic)
# ==========================================
@dp.message()
async def main_filter(message: types.Message):
    if message.chat.type == 'private': return
    
    # 1. GBan Check
    if await gban_col.find_one({"user_id": message.from_user.id}):
        try: await bot.ban_chat_member(message.chat.id, message.from_user.id)
        except: pass
        return

    # 2. Get Settings
    st = await get_settings(message.chat.id)
    
    # 3. Immunity Check
    is_auth = message.from_user.id in st.get('auth_users', [])
    is_adm = await is_admin(message.chat.id, message.from_user.id)
    
    # --- NSFW FILTER (Auth Checks Applied) ---
    if st.get('imagefilter') and message.content_type in [ContentType.PHOTO, ContentType.STICKER] and not is_adm:
        try:
            file_id = message.photo[-1].file_id if message.photo else message.sticker.file_id
            file = await bot.get_file(file_id)
            file_url = f"https://api.telegram.org/file/bot{API_TOKEN}/{file.file_path}"
            
            is_nsfw, _ = await check_nsfw_async(file_url)
            if is_nsfw:
                await message.delete()
                temp = await message.answer("🚫 **NSFW Detected!**")
                await asyncio.sleep(5)
                await temp.delete()
                return
        except Exception as e: logger.error(f"NSFW Check Fail: {e}")

    # --- TEXT FILTERS ---
    txt = message.text or message.caption or ""
    
    # Links (Auth Checks Applied)
    if st.get('nolinks') and not is_adm:
        if message.entities:
            for e in message.entities:
                if e.type in ['url', 'text_link', 'mention']:
                    try: await message.delete(); return
                    except: pass

    # Profanity (Auth Immune)
    if st.get('profanity') and txt and not is_auth and not is_adm:
        censored = await get_censored_text_async(txt)
        if censored:
            try:
                await message.delete()
                markup = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="➕ Add Me", url=f"https://t.me/{(await bot.get_me()).username}?startgroup=true"),
                    InlineKeyboardButton(text="📢 Updates", url=UPDATES_LINK)
                ]])
                user_link = f"[{escape_md(message.from_user.first_name)}](tg://user?id={message.from_user.id})"
                msg = f"🚫 Hey {user_link}, message removed.\n\n🔍 **Censored:**\n{censored}\n\nPlease be respectful."
                sent = await message.answer(msg, reply_markup=markup)
                await asyncio.sleep(30)
                await sent.delete()
            except: pass

@dp.message(F.new_chat_members)
async def on_join(message: types.Message):
    for m in message.new_chat_members:
        if m.id == (await bot.get_me()).id:
            await start_command(message)

# ==========================================
# 👑 SUDO LOGIC (Broadcast, Keys)
# ==========================================
@dp.message(Command("broadcast"))
async def broadcast(message: types.Message):
    if not await is_sudo(message.from_user.id): return
    
    msg = message.text.replace("/broadcast", "").strip()
    if not msg: return await message.reply("Message missing.")
    
    chats = await settings_col.distinct("chat_id")
    count = 0
    await message.reply(f"🚀 Broadcasting to {len(chats)} chats...")
    
    for chat in chats:
        try:
            await bot.send_message(chat, msg)
            count += 1
            await asyncio.sleep(0.05) 
        except: pass
    
    await message.reply(f"✅ Broadcast done. Sent to {count} chats.")

# --- SUDO COMMANDS MENU ---
@dp.message(Command("sudocommands"))
async def sudo_help(message: types.Message):
    if not await is_sudo(message.from_user.id): return
    text = """👑 **Sudo Commands List:**
`/gban <id> <reason>` - Global Ban
`/ungban <id>` - Global Unban
`/addsudo <id>` - Add new Sudo
`/remsudo <id>` - Remove Sudo
`/addapi <user> <secret>` - Add NSFW Key
`/addai <key>` - Add OpenRouter Key
`/logger on/off` - Log Channel
`/broadcast <msg>` - Send Broadcast"""
    await message.reply(text)

# --- START ---
@dp.message(CommandStart())
async def start_command(message: types.Message):
    if await gban_col.find_one({"user_id": message.from_user.id}): return
    
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add to Group ➕", url=f"https://t.me/{(await bot.get_me()).username}?startgroup=true")],
        [InlineKeyboardButton(text="Support", url=SUPPORT_LINK), InlineKeyboardButton(text="Updates", url=UPDATES_LINK)]
    ])
    
    caption = f"🔒 **Hello {escape_md(message.from_user.first_name)}!**\nI am a High-Performance Async Security Bot."
    try: await message.answer_animation("https://files.catbox.moe/3drb22.gif", caption=caption, reply_markup=markup)
    except: await message.answer(caption, reply_markup=markup)

# ==========================================
# 🏁 MAIN ENTRY POINT
# ==========================================
async def main():
    await refresh_caches()
    logger.info("⚡ ULTIMATE ASYNC BOT STARTED...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot Stopped.")
