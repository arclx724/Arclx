import telebot
from telebot import types
import pymongo
import time
import threading
import re
import requests
import os
import random
import datetime
import psutil
import sys

# --- 🛠️ FIX FOR TERMUX DNS ERROR ---
import dns.resolver
dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
dns.resolver.default_resolver.nameservers = ['8.8.8.8']

# ==========================================
# ⚙️ CONFIGURATION (Yahan Keys Daalo)
# ==========================================
API_TOKEN = '8238728169:AAF0oyGa5kBIrzfRP2v8AhJbfh2NIog23ds'
MONGO_URL = "mongodb+srv://arclx724_db_user:arclx724_db_user@cluster0.czhpczm.mongodb.net/?appName=Cluster0"
OWNER_ID = 8042205941

# 👇 YAHAN APNI DONO API KEYS PASTE KARO (Quotes ke andar)
SIGHT_USER = '222232214'   # SightEngine API User
SIGHT_SECRET = '3DHj7xorvBEZtPaLFJLwBP86L8qbzrN9' # SightEngine API Secret
AI_API_KEY = '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' # AI Key

LOG_CHANNEL_ID = "@c4llli" 
SUPPORT_LINK = "https://t.me/BillieSupport"
UPDATES_LINK = "https://t.me/c4llli"
START_TIME = time.time()

# --- DATABASE CONNECTION ---
print("🔄 System Booting...")
try:
    client = pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    db = client['GroupHelpClone']
    settings_col = db['settings']
    pending_delete_col = db['pending_deletes']
    config_col = db['config']
    sudo_col = db['sudoers']
    gban_col = db['gbans']
    print("✅ Database Connected!")
except Exception as e:
    print(f"❌ Connection Failed: {e}")
    sys.exit()

bot = telebot.TeleBot(API_TOKEN)

# --- 🗺️ COMMAND MAPPING ---
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

# ⚡ RAM CACHE
SETTINGS_CACHE = {}

def get_settings(chat_id):
    if chat_id in SETTINGS_CACHE:
        return SETTINGS_CACHE[chat_id]
    data = settings_col.find_one({"chat_id": chat_id})
    if not data: data = {}
    SETTINGS_CACHE[chat_id] = data
    return data

def update_setting_cache(chat_id, key, value):
    settings_col.update_one({"chat_id": chat_id}, {"$set": {key: value}}, upsert=True)
    if chat_id not in SETTINGS_CACHE: SETTINGS_CACHE[chat_id] = {}
    SETTINGS_CACHE[chat_id][key] = value

# --- HELPER FUNCTIONS ---
def log_event(text, to_channel=False):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {text}")
    with open("bot_logs.txt", "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {text}\n")
    
    if to_channel and LOG_CHANNEL_ID:
        conf = config_col.find_one({"key": "logger_active"})
        if conf and conf.get('status') is False: return
        try: bot.send_message(LOG_CHANNEL_ID, f"🔔 **LOG:**\n{text}", parse_mode='Markdown')
        except: pass

def delete_delayed(chat_id, message_id, sec=30):
    time.sleep(sec)
    try: bot.delete_message(chat_id, message_id)
    except: pass

def is_sudo(user_id):
    if user_id == OWNER_ID: return True
    if sudo_col.find_one({"user_id": user_id}): return True
    return False

def get_target_user(message):
    if message.reply_to_message: return message.reply_to_message.from_user
    args = message.text.split()
    if len(args) < 2: return None
    input_str = args[1]
    if message.entities:
        for entity in message.entities:
            if entity.type == 'text_mention' and entity.user: return entity.user
    if input_str.startswith("@"):
        try: return bot.get_chat(input_str)
        except: return None
    if input_str.isdigit():
        try: return bot.get_chat_member(message.chat.id, int(input_str)).user
        except: return None
    return None

def escape_md(text):
    if not text: return ""
    return text.replace("*", "").replace("_", "").replace("`", "").replace("[", "")

def is_admin(chat_id, user_id):
    if is_sudo(user_id): return True
    try:
        member = bot.get_chat_member(chat_id, user_id)
        return member.status in ['administrator', 'creator']
    except: return False

# ==========================================
# 🧠 API ENGINES (Hardcoded Keys Version)
# ==========================================
def check_nsfw(file_path):
    # Check if keys are filled
    if 'INSERT' in SIGHT_USER: return False, "No Key"
    
    url = 'https://api.sightengine.com/1.0/check.json'
    try:
        with open(file_path, 'rb') as binary_file:
            files_data = {'media': ('image.jpg', binary_file, 'image/jpeg')}
            data = {'models': 'nudity', 'api_user': SIGHT_USER, 'api_secret': SIGHT_SECRET}
            response = requests.post(url, files=files_data, data=data, timeout=10)
        
        result = response.json()
        if result['status'] == 'success':
            nudity = result.get('nudity', {})
            # Sensitivity Logic
            if (nudity.get('raw', 0) > 0.60) or (nudity.get('partial', 0) > 0.70):
                return True, "NSFW Content"
            return False, "Safe"
    except Exception as e:
        print(f"API Error: {e}")
        return False, "Error"
    return False, "Error"

def get_censored_text(text):
    if 'INSERT' in AI_API_KEY: return None
    
    system_prompt = "You are a Content Filter. Detect Profanity/Abuse in English/Hindi/Hinglish. Reply 'SAFE' if none. If found, return text with ||badword||."
    try:
        headers = {"Authorization": f"Bearer {AI_API_KEY}"}
        json_data = {
            "model": "google/gemini-2.0-flash-001",
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": text}]
        }
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=json_data, timeout=3)
        if response.status_code == 200:
            result = response.json()['choices'][0]['message']['content'].strip()
            if "SAFE" in result: return None
            return result
    except: pass
    return None

# ==========================================
# 👑 SUDO COMMANDS
# ==========================================
@bot.message_handler(commands=['gban'])
def gban(message):
    if not is_sudo(message.from_user.id): return
    try:
        tid = int(message.text.split()[1])
        gban_col.update_one({"user_id": tid}, {"$set": {"reason": "Abuse"}}, upsert=True)
        bot.reply_to(message, f"🚫 **GBanned:** `{tid}`")
        def worker():
            for g in settings_col.find({}):
                try: bot.ban_chat_member(g['chat_id'], tid)
                except: pass
        threading.Thread(target=worker).start()
    except: bot.reply_to(message, "Usage: `/gban ID`")

@bot.message_handler(commands=['ungban'])
def ungban(message):
    if not is_sudo(message.from_user.id): return
    try:
        tid = int(message.text.split()[1])
        gban_col.delete_one({"user_id": tid})
        bot.reply_to(message, f"✅ **Un-GBanned:** `{tid}`")
    except: pass

@bot.message_handler(commands=['addsudo', 'remsudo', 'broadcast', 'logs', 'logger', 'sudocommands', 'stats', 'maintenance'])
def sudo_cmds(message):
    if not is_sudo(message.from_user.id): return
    cmd = message.text.split()[0]

    if 'sudocommands' in cmd:
        bot.reply_to(message, "👑 **Sudo:**\n/gban, /ungban\n/addsudo, /remsudo\n/broadcast, /logs\n/logger on/off")
    elif 'logger' in cmd:
        val = True if 'on' in message.text else False
        config_col.update_one({"key": "logger_active"}, {"$set": {"status": val}}, upsert=True)
        bot.reply_to(message, f"📢 Logger: {val}")
    elif 'broadcast' in cmd:
        msg = message.text.replace("/broadcast", "").strip()
        if not msg: return
        chats = settings_col.distinct("chat_id")
        bot.reply_to(message, f"🚀 Sending to {len(chats)} chats...")
        def worker():
            for c in chats:
                try: bot.send_message(c, msg); time.sleep(0.1)
                except: pass
        threading.Thread(target=worker).start()
        bot.reply_to(message, "✅ Broadcast Started.")
    elif 'stats' in cmd:
        uptime = str(datetime.timedelta(seconds=int(time.time() - START_TIME)))
        bot.reply_to(message, f"📊 **Stats:**\nUP: `{uptime}`\nCPU: `{psutil.cpu_percent()}%`")

# ==========================================
# 🛡️ ADMIN COMMANDS
# ==========================================
@bot.message_handler(commands=['promote', 'fullpromote', 'demote', 'ban', 'unban', 'mute', 'unmute', 'auth', 'unauth', 'authlist', 'unauthall'])
def admin_actions(message):
    if message.chat.type == 'private': return
    
    cmd = message.text.split()[0].lower()
    chat = message.chat.id
    
    # Auth List
    if '/authlist' in cmd:
        if not is_admin(chat, message.from_user.id): return
        st = get_settings(chat)
        if not st.get('auth_users'): return bot.reply_to(message, "📂 **Empty.**")
        return bot.reply_to(message, f"🛡️ **Auth Users:** `{st.get('auth_users', [])}`")
    
    # Unauth All
    if '/unauthall' in cmd:
        try:
            if bot.get_chat_member(chat, message.from_user.id).status == 'creator' or is_sudo(message.from_user.id):
                settings_col.update_one({"chat_id": chat}, {"$set": {"auth_users": []}})
                # Clear Cache for this chat
                if chat in SETTINGS_CACHE: del SETTINGS_CACHE[chat]
                bot.reply_to(message, "🗑️ All Auth users removed.")
            else: bot.reply_to(message, "❌ Only Owner.")
        except: pass
        return

    target = get_target_user(message)
    if not target: return bot.reply_to(message, "❌ User Not Found.")
    
    if not is_admin(chat, message.from_user.id): return

    uid = target.id
    name = escape_md(target.first_name)
    title = "Admin"
    if len(message.text.split()) > 2: title = " ".join(message.text.split()[2:])[:16]

    try:
        if '/promote' in cmd:
            bot.promote_chat_member(chat, uid, can_invite_users=True, can_delete_messages=True, can_restrict_members=True, can_pin_messages=True)
            try: bot.set_chat_administrator_custom_title(chat, uid, title)
            except: pass
            bot.reply_to(message, f"✅ **Promoted:** {name}\n🏷 `{title}`", parse_mode='Markdown')
        elif '/fullpromote' in cmd:
            bot.promote_chat_member(chat, uid, True, True, True, True, True, True, True, True)
            try: bot.set_chat_administrator_custom_title(chat, uid, title)
            except: pass
            bot.reply_to(message, f"👑 **Full Promoted:** {name}", parse_mode='Markdown')
        elif '/demote' in cmd:
            bot.promote_chat_member(chat, uid, False, False, False, False, False, False, False, False)
            bot.reply_to(message, f"⬇️ **Demoted:** {name}", parse_mode='Markdown')
        elif '/auth' in cmd:
            settings_col.update_one({"chat_id": chat}, {"$addToSet": {"auth_users": uid}}, upsert=True)
            if chat in SETTINGS_CACHE: SETTINGS_CACHE[chat].setdefault('auth_users', []).append(uid)
            bot.reply_to(message, f"✅ **Authorized:** {name}")
        elif '/unauth' in cmd:
            settings_col.update_one({"chat_id": chat}, {"$pull": {"auth_users": uid}})
            if chat in SETTINGS_CACHE: del SETTINGS_CACHE[chat] # Force Refresh
            bot.reply_to(message, f"🚫 **Un-Authorized:** {name}")
        elif '/ban' in cmd:
            bot.ban_chat_member(chat, uid)
            bot.reply_to(message, f"🚫 **Banned:** {name}")
        elif '/unban' in cmd:
            bot.unban_chat_member(chat, uid, only_if_banned=True)
            bot.reply_to(message, f"✅ **Unbanned:** {name}")
        elif '/mute' in cmd:
            bot.restrict_chat_member(chat, uid, can_send_messages=False)
            bot.reply_to(message, f"🔇 **Muted:** {name}")
        elif '/unmute' in cmd:
            bot.restrict_chat_member(chat, uid, can_send_messages=True, can_send_media_messages=True)
            bot.reply_to(message, f"🔊 **Unmuted:** {name}")
    except Exception as e: bot.reply_to(message, f"⚠️ Error: {e}")

@bot.message_handler(commands=['stat', 'status'])
def status_cmd(message):
    if message.chat.type == 'private': return
    if not is_admin(message.chat.id, message.from_user.id): return
    
    st = get_settings(message.chat.id)
    flist = ""
    order = ["antiflood", "imagefilter", "noevents", "nolinks", "noforwards", "profanity", "nobots"]
    for k in order:
        icon = "✅" if st.get(k) else "▫️"
        flist += f"{icon} {k.capitalize()} filter /{k}\n"
    
    bot.reply_to(message, f"**{escape_md(bot.get_me().first_name)}**\n{escape_md(message.chat.title)}\n**Filters:**\n{flist}", parse_mode='Markdown')

@bot.message_handler(commands=list(CMD_MAP.keys()))
def toggles(message):
    if message.chat.type == 'private': return
    if not is_admin(message.chat.id, message.from_user.id): return
    
    cmd = message.text.split()[0][1:]
    val = True if 'on' in message.text.lower() else False
    update_setting_cache(message.chat.id, cmd, val)
    
    st = "enabled" if val else "disabled"
    bot.reply_to(message, f"{escape_md(message.chat.title)}, {st} {cmd} ✅." if val else f"{st} {cmd}.")

# --- MAIN FILTER ---
@bot.edited_message_handler(func=lambda m: True)
def on_edit(message):
    if message.chat.type == 'private': return
    st = get_settings(message.chat.id)
    if not st.get('noedit'): return
    if message.from_user.id in st.get('auth_users', []) or is_sudo(message.from_user.id): return
    try:
        bot.delete_message(message.chat.id, message.message_id)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("Updates", url=UPDATES_LINK))
        m = bot.send_message(message.chat.id, f"🚨 **Security Alert!**\n{message.from_user.first_name}, edits not allowed!", reply_markup=markup)
        threading.Thread(target=delete_delayed, args=(message.chat.id, m.message_id, 60)).start()
    except: pass

@bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'video', 'sticker', 'caption', 'new_chat_members'])
def main_filter(message):
    # Welcome Bot
    if message.content_type == 'new_chat_members':
        for m in message.new_chat_members:
            if m.id == bot.get_me().id: start(message)
        return

    if message.chat.type == 'private': return
    if check_gban(message.from_user.id):
        try: bot.ban_chat_member(message.chat.id, message.from_user.id); return
        except: pass

    st = get_settings(message.chat.id)
    is_auth = message.from_user.id in st.get('auth_users', [])
    is_adm = is_admin(message.chat.id, message.from_user.id)
    txt = message.text or message.caption or ""

    # NSFW (Image Filter) - Hardcoded Keys Used
    if st.get('imagefilter') and message.content_type in ['photo', 'sticker'] and not is_adm:
        try:
            fid = message.photo[-1].file_id if message.photo else message.sticker.file_id
            finfo = bot.get_file(fid); down = bot.download_file(finfo.file_path)
            temp = f"scan_{message.message_id}.jpg"
            with open(temp, "wb") as f: f.write(down)
            is_nsfw, res = check_nsfw(temp)
            if os.path.exists(temp): os.remove(temp)
            if is_nsfw:
                bot.delete_message(message.chat.id, message.message_id)
                m = bot.send_message(message.chat.id, "🚫 **NSFW Detected!**")
                threading.Thread(target=delete_delayed, args=(message.chat.id, m.message_id, 5)).start()
                return
        except: pass

    # Text Filters
    if st.get('nolinks') and not is_adm:
        if message.entities or message.caption_entities:
            for e in (message.entities or []) + (message.caption_entities or []):
                if e.type in ['url', 'text_link', 'mention']:
                    try: bot.delete_message(message.chat.id, message.message_id); return
                    except: pass

    if st.get('profanity') and txt and not is_auth and not is_adm:
        censored = get_censored_text(txt)
        if censored:
            try:
                bot.delete_message(message.chat.id, message.message_id)
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("Updates", url=UPDATES_LINK))
                msg = f"🚫 Hey {message.from_user.first_name}, message removed.\n\n🔍 **Censored:**\n{censored}"
                m = bot.send_message(message.chat.id, msg, reply_markup=markup)
                threading.Thread(target=delete_delayed, args=(message.chat.id, m.message_id, 30)).start()
            except: pass

@bot.message_handler(commands=['start', 'reload'])
def start(message):
    if check_gban(message.from_user.id): return
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("➕ Add to Group ➕", url=f"https://t.me/{bot.get_me().username}?startgroup=true"))
    markup.add(types.InlineKeyboardButton("Support", url=SUPPORT_LINK), types.InlineKeyboardButton("Updates", url=UPDATES_LINK))
    
    caption = f"🔒 **Hello {escape_md(message.from_user.first_name)}!**\nI am a High-Performance Security Bot."
    try: bot.send_animation(message.chat.id, "https://files.catbox.moe/3drb22.gif", caption=caption, parse_mode='Markdown', reply_markup=markup)
    except: bot.reply_to(message, caption, reply_markup=markup)

print("⚡ BOT ONLINE (Legacy Stable + New Commands)...")
bot.infinity_polling(timeout=40, skip_pending=True)
                                                         
