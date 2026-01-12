import telebot
from telebot import types
import pymongo
import time
import threading
import re
import requests
import base64
import os
import random

# --- 🛠️ FIX FOR TERMUX DNS ERROR ---
import dns.resolver
dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
dns.resolver.default_resolver.nameservers = ['8.8.8.8']

# --- CONFIGURATION ---
API_TOKEN = '8238728169:AAF0oyGa5kBIrzfRP2v8AhJbfh2NIog23ds'
MONGO_URL = "mongodb+srv://arclx724_db_user:arclx724_db_user@cluster0.czhpczm.mongodb.net/?appName=Cluster0"
OPENROUTER_API_KEY = "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"

# --- SIGHTENGINE CONFIG (NSFW) ---
SIGHT_USER = "222232214"
SIGHT_SECRET = "3DHj7xorvBEZtPaLFJLwBP86L8qbzrN9"

# --- LOCAL PROFANITY LIST ---
BAD_WORDS = [
    "fuck", "bitch", "bastard", "shit", "asshole", "dick", "pussy", "cunt", 
    "whore", "slut", "fucker", "motherfucker", "cock", "penis", "vagina", 
    "nigger", "nigga", "fag", "faggot", "sex", "boobs", "porn", "xxx",
    "randi", "bhosdi", "madarchod", "behenchod", "chutiya", "gand", "lodu"
]

# --- DATABASE CONNECTION ---
print("🔄 System Booting...")
try:
    client = pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    db = client['GroupHelpClone']
    limits_col = db['admin_limits']
    warns_col = db['warns']
    settings_col = db['settings'] 
    pending_delete_col = db['pending_deletes']
    print("✅ Database Connected!")
except Exception as e:
    print(f"❌ Connection Failed: {e}")
    exit()

bot = telebot.TeleBot(API_TOKEN)

# --- 🧹 BACKGROUND CLEANER THREAD ---
def media_cleaner_loop():
    print("🧹 Cleaner Thread Started...")
    while True:
        try:
            current_time = time.time()
            expired_msgs = pending_delete_col.find({"delete_at": {"$lte": current_time}})
            for msg in expired_msgs:
                try: bot.delete_message(msg['chat_id'], msg['msg_id'])
                except: pass
                pending_delete_col.delete_one({"_id": msg['_id']})
            time.sleep(5)
        except: time.sleep(5)

threading.Thread(target=media_cleaner_loop, daemon=True).start()

# --- 🛡️ HELPERS & AI ---
def escape_md(text):
    if not text: return ""
    return text.replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")

def delete_delayed(chat_id, message_id, sec=30):
    time.sleep(sec)
    try: bot.delete_message(chat_id, message_id)
    except: pass

# --- SIGHTENGINE NSFW CHECKER ---
def check_nsfw(file_path):
    url = 'https://api.sightengine.com/1.0/check.json'
    try:
        with open(file_path, 'rb') as binary_file:
            files_data = {'media': ('image.jpg', binary_file, 'image/jpeg')}
            response = requests.post(
                url, files=files_data,
                data={'models': 'nudity', 'api_user': SIGHT_USER, 'api_secret': SIGHT_SECRET},
                timeout=10
            )
        result = response.json()
        if result['status'] == 'success':
            nudity = result.get('nudity', {})
            raw = nudity.get('raw', 0)
            partial = nudity.get('partial', 0)
            
            # Threshold: 60% Raw or 70% Partial
            if raw > 0.60 or partial > 0.70:
                return True, f"Nudity Score: {int(raw*100)}%"
        return False, "Safe"
    except Exception as e:
        print(f"API Error: {e}")
        return False, "Error"

def check_ai_profanity(text):
    try:
        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            json={
                "model": "google/gemini-2.0-flash-001",
                "messages": [{"role": "system", "content": "Check for profanity/hate/toxicity. Reply ONLY 'YES' or 'NO'."}, {"role": "user", "content": text}]
            }, timeout=2
        )
        if response.status_code == 200:
            return "YES" in response.json()['choices'][0]['message']['content'].strip().upper()
    except: pass
    return False

def get_user_id(message):
    if message.reply_to_message:
        return message.reply_to_message.from_user.id, escape_md(message.reply_to_message.from_user.first_name)
    args = message.text.split()
    if len(args) > 1:
        if args[1].isdigit(): return int(args[1]), "User"
        if args[1].startswith("@"):
            try:
                user = bot.get_chat(args[1])
                return user.id, escape_md(user.first_name or "User")
            except: return None, "ERROR_USERNAME"
    return None, None

def check_admin_limit(chat_id, user_id):
    try:
        if bot.get_chat_member(chat_id, user_id).status == 'creator': return True, 0
    except: pass
    current_time = time.time()
    record = limits_col.find_one({"chat_id": chat_id, "user_id": user_id})
    if record:
        if current_time > record['reset_time']:
            limits_col.update_one({"_id": record['_id']}, {"$set": {"count": 1, "reset_time": current_time + 86400}})
            return True, 1
        elif record['count'] < 10:
            limits_col.update_one({"_id": record['_id']}, {"$inc": {"count": 1}})
            return True, record['count'] + 1
        else: return False, 10
    else:
        limits_col.insert_one({"chat_id": chat_id, "user_id": user_id, "count": 1, "reset_time": current_time + 86400})
        return True, 1

def is_admin(chat_id, user_id):
    try:
        m = bot.get_chat_member(chat_id, user_id)
        return m.status in ['administrator', 'creator']
    except: return False

def can_change_info(chat_id, user_id):
    try:
        m = bot.get_chat_member(chat_id, user_id)
        if m.status == 'creator': return True
        if m.status == 'administrator' and m.can_change_info: return True
        return False
    except: return False

def get_settings(chat_id):
    data = settings_col.find_one({"chat_id": chat_id})
    if not data:
        return {
            "noevents": False, "nobots": False, "nolinks": False, "noforwards": False,
            "nocontacts": False, "nolocations": False, "nocommands": False, "nohashtags": False,
            "biolink": False, "novoice": False, "profanity": False, "warningbans": False, "imagefilter": False,
            "flood": None, "auth_users": [], "delay": 0, "noedit": False, "blacklist": [], "whitelist": [], "blacklist_active": False
        }
    return data

def update_setting(chat_id, key, value):
    settings_col.update_one({"chat_id": chat_id}, {"$set": {key: value}}, upsert=True)

# --- 🚀 START COMMAND ---
@bot.message_handler(commands=['start'])
def start(message):
    user_name = escape_md(message.from_user.first_name)
    bot_name = escape_md(bot.get_me().first_name)
    caption = f"""👋 **Welcome {user_name} | {bot_name}!**

☁️ @{escape_md(bot.get_me().username)} allows you to **manage** your groups.

🔒 **Security:** Anti-Spam, AI Filters, Moderation Tools.
🔧 **Version:** 3.7 (SightEngine NSFW Integrated)"""

    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("➕ Add me to a Group ➕", url=f"https://t.me/{bot.get_me().username}?startgroup=true"))
    markup.row(types.InlineKeyboardButton("⚙️ Settings", callback_data="settings"), types.InlineKeyboardButton("ℹ️ Info", callback_data="info"))
    
    try:
        bot.send_video(message.chat.id, "https://files.catbox.moe/ezqst2.mp4", caption=caption, parse_mode='Markdown', reply_markup=markup)
    except:
        bot.reply_to(message, caption, parse_mode='Markdown', reply_markup=markup)

# --- ⚙️ TOGGLES ---
@bot.message_handler(commands=['noevents', 'nobots', 'nolinks', 'noforwards', 'nocontacts', 'nolocations', 'nocommands', 'nohashtags', 'noedit', 'biolink', 'novoice', 'profanity', 'warningbans', 'imagefilter'])
def toggle_handler(message):
    if message.chat.type == 'private': return
    if not can_change_info(message.chat.id, message.from_user.id): 
        msg = bot.reply_to(message, "❌ Admin Rights Required.")
        threading.Thread(target=delete_delayed, args=(message.chat.id, msg.message_id, 10)).start()
        return
    
    cmd = message.text.split()[0][1:] 
    args = message.text.split()
    
    if len(args) < 2 or args[1].lower() not in ['on', 'off']: 
        status = "ENABLED" if get_settings(message.chat.id).get(cmd, False) else "DISABLED"
        msg = bot.reply_to(message, f"ℹ️ **Current Status:** `{status}`\nUsage: `/{cmd} on` or `off`", parse_mode='Markdown')
        threading.Thread(target=delete_delayed, args=(message.chat.id, msg.message_id, 30)).start()
        return

    value = True if args[1].lower() == 'on' else False
    update_setting(message.chat.id, cmd, value)
    
    if cmd == "imagefilter":
        if value:
            msg = bot.reply_to(message, "✅ **NSFW Filter Enabled!**\nThe bot will now auto-delete NSFW images and videos.")
        else:
            msg = bot.reply_to(message, "⚠️ **NSFW Filter Disabled!**\nThe bot is now sleeping and won't scan media.")
    else:
        user_name = escape_md(message.from_user.first_name)
        status = "enabled" if value else "disabled"
        filter_name = cmd.replace("no", "") 
        if cmd == "biolink": filter_name = "bio link"
        elif cmd == "warningbans": filter_name = "warning bans"
        msg = bot.reply_to(message, f"ㅤ➺ {user_name}, {status} {filter_name} filter ✅.", parse_mode='Markdown')

    threading.Thread(target=delete_delayed, args=(message.chat.id, msg.message_id, 30)).start()

# --- 🚫 BLACKLIST / WHITELIST ---
@bot.message_handler(commands=['blacklist', 'blacklist_add', 'blacklist_remove', 'blacklist_clear', 'listblacklist'])
def blacklist_cmds(message):
    if message.chat.type == 'private': return
    if not can_change_info(message.chat.id, message.from_user.id): return bot.reply_to(message, "❌ Admin Rights Required.")
    cmd = message.text.split()[0]
    chat_id = message.chat.id
    msg = None
    
    if 'add' in cmd:
        try:
            items = message.text.split(None, 1)[1]; item_list = [i.strip() for i in items.split(',')]
            settings_col.update_one({"chat_id": chat_id}, {"$addToSet": {"blacklist": {"$each": item_list}}}, upsert=True)
            msg = bot.reply_to(message, f"✅ Added.")
        except: msg = bot.reply_to(message, "Usage: `/blacklist_add word`")
    elif 'remove' in cmd:
        try:
            item = message.text.split(None, 1)[1].strip()
            settings_col.update_one({"chat_id": chat_id}, {"$pull": {"blacklist": item}}); msg = bot.reply_to(message, f"✅ Removed.")
        except: msg = bot.reply_to(message, "Usage: `/blacklist_remove word`")
    elif 'clear' in cmd:
        settings_col.update_one({"chat_id": chat_id}, {"$set": {"blacklist": []}}); msg = bot.reply_to(message, "🗑️ Cleared.")
    elif 'list' in cmd:
        st = get_settings(chat_id)
        if not st.get('blacklist'): msg = bot.reply_to(message, "📂 Empty.")
        else:
            safe_list = [escape_md(x) for x in st['blacklist']]
            msg = bot.reply_to(message, f"🚫 **Blacklist:**\n" + ", ".join([f"`{x}`" for x in safe_list]), parse_mode='Markdown')
    else: 
        args = message.text.split()
        if len(args) < 2: msg = bot.reply_to(message, "Usage: `/blacklist on/off`")
        else:
            val = True if args[1].lower() == 'on' else False
            update_setting(chat_id, "blacklist_active", val)
            user_name = escape_md(message.from_user.first_name)
            status = "enabled" if val else "disabled"
            msg = bot.reply_to(message, f"ㅤ➺ {user_name}, {status} blacklist filter ✅.", parse_mode='Markdown')
            
    if msg: threading.Thread(target=delete_delayed, args=(chat_id, msg.message_id, 30)).start()

@bot.message_handler(commands=['whitelist', 'whitelist_add', 'whitelist_remove', 'whitelist_clear'])
def whitelist_cmds(message):
    if message.chat.type == 'private': return
    if not can_change_info(message.chat.id, message.from_user.id): return bot.reply_to(message, "❌ Admin Rights Required.")
    cmd = message.text.split()[0]
    chat_id = message.chat.id
    msg = None
    if 'add' in cmd:
        try:
            items = message.text.split(None, 1)[1]; item_list = [i.strip() for i in items.split(',')]
            settings_col.update_one({"chat_id": chat_id}, {"$addToSet": {"whitelist": {"$each": item_list}}}, upsert=True)
            msg = bot.reply_to(message, f"✅ Added.")
        except: pass
    elif 'remove' in cmd:
        try:
            item = message.text.split(None, 1)[1].strip()
            settings_col.update_one({"chat_id": chat_id}, {"$pull": {"whitelist": item}}); msg = bot.reply_to(message, "✅ Removed.")
        except: pass
    elif 'clear' in cmd:
        settings_col.update_one({"chat_id": chat_id}, {"$set": {"whitelist": []}}); msg = bot.reply_to(message, "🗑️ Cleared.")
    else:
        st = get_settings(chat_id)
        if not st.get('whitelist'): msg = bot.reply_to(message, "📂 Empty.")
        else:
            safe_list = [escape_md(x) for x in st['whitelist']]
            msg = bot.reply_to(message, f"✅ **Whitelist:**\n" + ", ".join([f"`{x}`" for x in safe_list]), parse_mode='Markdown')
    if msg: threading.Thread(target=delete_delayed, args=(chat_id, msg.message_id, 30)).start()

# --- 🌊 ANTI-FLOOD ---
@bot.message_handler(commands=['antiflood'])
def antiflood_setup(message):
    if message.chat.type == 'private': return
    if not can_change_info(message.chat.id, message.from_user.id): return bot.reply_to(message, "❌ Admin Rights Required.")
    args = message.text.split()
    msg = None
    if len(args) > 1 and args[1].lower() == 'off': 
        update_setting(message.chat.id, "flood", None)
        msg = bot.reply_to(message, "✅ **Disabled.**")
    elif len(args) >= 4 and args[1].isdigit() and args[3].isdigit():
        update_setting(message.chat.id, "flood", {"limit": int(args[1]), "time": int(args[3])})
        msg = bot.reply_to(message, f"✅ **Set:** Max {args[1]} msgs in {args[3]}s.")
    else: msg = bot.reply_to(message, "Usage: `/antiflood 3 per 20`")
    if msg: threading.Thread(target=delete_delayed, args=(message.chat.id, msg.message_id, 30)).start()

# --- 🔐 AUTH ---
@bot.message_handler(commands=['auth'])
def auth_user(message):
    if not can_change_info(message.chat.id, message.from_user.id): return
    args = message.text.split()
    if len(args) > 1 and args[1].lower() in ['on', 'off']:
        if bot.get_chat_member(message.chat.id, message.from_user.id).status != 'creator': return bot.reply_to(message, "❌ Owner Only.")
        val = True if args[1] == 'on' else False
        update_setting(message.chat.id, 'noedit', val)
        msg = bot.reply_to(message, f"✅ **Edit Protection {'ON' if val else 'OFF'}.**")
        threading.Thread(target=delete_delayed, args=(message.chat.id, msg.message_id, 30)).start()
        return
    user_id, name = get_user_id(message)
    if not user_id: return bot.reply_to(message, "Reply to user.")
    settings_col.update_one({"chat_id": message.chat.id},{"$addToSet": {"auth_users": user_id}}, upsert=True)
    msg = bot.reply_to(message, f"✅ **Authorized:** {name}", parse_mode='Markdown')
    threading.Thread(target=delete_delayed, args=(message.chat.id, msg.message_id, 30)).start()

@bot.message_handler(commands=['unauth', 'authusers', 'clearauthusers'])
def auth_manage(message):
    if not can_change_info(message.chat.id, message.from_user.id): return
    cmd = message.text.split()[0]
    msg = None
    if 'clear' in cmd: update_setting(message.chat.id, "auth_users", []); msg = bot.reply_to(message, "🗑️ Cleared.")
    elif 'users' in cmd:
        st = get_settings(message.chat.id)
        if not st.get('auth_users'): msg = bot.reply_to(message, "📂 Empty.")
        else: msg = bot.reply_to(message, f"Users: {len(st['auth_users'])}")
    else:
        user_id, name = get_user_id(message)
        if user_id: 
            settings_col.update_one({"chat_id": message.chat.id},{"$pull": {"auth_users": user_id}})
            msg = bot.reply_to(message, "🚫 Unauth.")
    if msg: threading.Thread(target=delete_delayed, args=(message.chat.id, msg.message_id, 30)).start()

# --- 👑 ADMIN (PROMOTE / FULLPROMOTE / DEMOTE) ---
@bot.message_handler(commands=['promote', 'fullpromote', 'demote'])
def admin_promotion(message):
    if message.chat.type == 'private': return
    
    # 1. Bot Power Check (Can I promote?)
    try:
        bot_member = bot.get_chat_member(message.chat.id, bot.get_me().id)
        if not bot_member.can_promote_members:
            msg = bot.reply_to(message, "❌ I need **'Add New Admins'** rights!")
            threading.Thread(target=delete_delayed, args=(message.chat.id, msg.message_id, 10)).start()
            return
    except: return

    # 2. User Power Check (Can YOU promote?)
    if not is_admin(message.chat.id, message.from_user.id): return
    try:
        user_admin = bot.get_chat_member(message.chat.id, message.from_user.id)
        if user_admin.status != 'creator' and not user_admin.can_promote_members:
            msg = bot.reply_to(message, "❌ You don't have promote rights.")
            threading.Thread(target=delete_delayed, args=(message.chat.id, msg.message_id, 10)).start()
            return
    except: return

    # Anti-Nuke Check
    allowed, count = check_admin_limit(message.chat.id, message.from_user.id)
    if not allowed: 
        msg = bot.reply_to(message, "🛑 **Limit Reached!**")
        threading.Thread(target=delete_delayed, args=(message.chat.id, msg.message_id, 10)).start()
        return

    # 3. Get Target User
    user_id, name = get_user_id(message)
    if not user_id: 
        msg = bot.reply_to(message, "❌ Reply to user or use `/promote @user`")
        threading.Thread(target=delete_delayed, args=(message.chat.id, msg.message_id, 10)).start()
        return

    cmd = message.text.split()[0].lower()
    
    #4. Extract Custom Title
    custom_title = "Admin"
    msg_args = message.text.split()
    if len(msg_args) > 1:
        if message.reply_to_message: custom_title = " ".join(msg_args[1:]) # /promote Title (Reply)
        elif len(msg_args) > 2: custom_title = " ".join(msg_args[2:]) # /promote @user Title
    
    if len(custom_title) > 16: custom_title = custom_title[:16]

    try:
        if '/promote' in cmd:
            # Limited Rights (Standard Mod)
            bot.promote_chat_member(
                message.chat.id, user_id,
                can_change_info=False,
                can_invite_users=True,
                can_delete_messages=True,
                can_restrict_members=True,
                can_pin_messages=True,
                can_promote_members=False, # Limited
                can_manage_video_chats=True,
                can_manage_chat=False
            )
            try: bot.set_chat_administrator_custom_title(message.chat.id, user_id, custom_title)
            except: pass
            
            bot.reply_to(message, f"✅ **Promoted Successfully**\n👤 {name}\n🏷 `{custom_title}`", parse_mode='Markdown')

        elif '/fullpromote' in cmd:
            #All Rights
            bot.promote_chat_member(
                message.chat.id, user_id,
                can_change_info=True,
                can_invite_users=True,
                can_delete_messages=True,
                can_restrict_members=True,
                can_pin_messages=True,
                can_promote_members=True,
                can_manage_video_chats=True,
                can_manage_chat=True
            )
            try: bot.set_chat_administrator_custom_title(message.chat.id, user_id, custom_title)
            except: pass
            
            bot.reply_to(message, f"👑 **Full Promoted Successfully**\n👤 {name}\n🏷 `{custom_title}`", parse_mode='Markdown')

        elif '/demote' in cmd:
            # Dismiss (All False)
            bot.promote_chat_member(
                message.chat.id, user_id,
                can_change_info=False,
                can_invite_users=False,
                can_delete_messages=False,
                can_restrict_members=False,
                can_pin_messages=False,
                can_promote_members=False,
                can_manage_video_chats=False,
                can_manage_chat=False
            )
            try: bot.set_chat_administrator_custom_title(message.chat.id, user_id, "")
            except: pass
            
            bot.reply_to(message, f"✅ **Admin Dismissed Successfully**\n👤 {name}\n⬇️ Now a normal member.", parse_mode='Markdown')

    except Exception as e:
        bot.reply_to(message, f"⚠️ Error: `{e}`")


# --- 🛡️ MAIN FILTER ---
@bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'video', 'document', 'sticker', 'contact', 'location', 'animation', 'audio', 'voice', 'caption'])
def main_filter(message):
    if message.chat.type == 'private': return
    chat_id = message.chat.id
    user_id = message.from_user.id
    st = get_settings(chat_id)
    is_auth = user_id in st.get('auth_users', [])
    is_admin_user = is_admin(chat_id, user_id)
    is_immune = is_admin_user or is_auth
    text_content = message.text or message.caption or ""

    # --- 🔞 IMAGE FILTER (SIGHTENGINE) ---
    if st.get('imagefilter') and not is_auth:
        file_data = None
        media_type = "File"
        temp_filename = f"scan_{chat_id}_{user_id}_{random.randint(1000,9999)}.jpg"
        
        try:
            should_scan = False
            if message.content_type == 'photo':
                file_info = bot.get_file(message.photo[-1].file_id)
                should_scan = True; media_type = "Photo"
            elif message.content_type == 'sticker' and (message.sticker.is_animated or message.sticker.is_video):
                if message.sticker.thumb:
                    file_info = bot.get_file(message.sticker.thumb.file_id)
                    should_scan = True; media_type = "Sticker"
            elif message.content_type == 'video' and message.video.thumb:
                file_info = bot.get_file(message.video.thumb.file_id)
                should_scan = True; media_type = "Video"
            
            if should_scan:
                file_bytes = bot.download_file(file_info.file_path)
                with open(temp_filename, "wb") as f:
                    f.write(file_bytes)
                
                is_nsfw, reason = check_nsfw(temp_filename)
                
                if os.path.exists(temp_filename):
                    os.remove(temp_filename)

                if is_nsfw:
                    try:
                        bot.delete_message(chat_id, message.message_id)
                        warn_msg = bot.send_message(chat_id, f"🚫 **NSFW {media_type} Deleted!**\nUser: {escape_md(message.from_user.first_name)}\nReason: {reason}", parse_mode='Markdown')
                        threading.Thread(target=delete_delayed, args=(chat_id, warn_msg.message_id, 60)).start()
                        
                        # Database Warn Logic
                        if st.get('warningbans'):
                            db_key = {"chat_id": chat_id, "user_id": user_id}
                            w = warns_col.find_one(db_key); count = w['count'] + 1 if w else 1
                            if count >= 3: 
                                bot.ban_chat_member(chat_id, user_id)
                                warns_col.delete_one(db_key)
                                bot.send_message(chat_id, f"🚫 **Banned:** (NSFW 3/3)", parse_mode='Markdown')
                            else: 
                                warns_col.update_one(db_key, {"$set": {"count": count}}, upsert=True)
                        return # Stop further processing
                    except: pass
        except Exception as e:
            if os.path.exists(temp_filename): os.remove(temp_filename)
            print(f"Scan Error: {e}")

    # --- OTHER FILTERS ---
    if st.get('novoice') and message.content_type == 'voice' and not is_auth:
        try: bot.delete_message(chat_id, message.message_id); return
        except: pass
    if st.get('nocommands') and text_content.startswith('/') and not is_auth:
        try: bot.delete_message(chat_id, message.message_id); return
        except: pass
    if st.get('biolink') and not is_immune:
        try:
            full_user = bot.get_chat(user_id); bio = full_user.bio if full_user.bio else ""
            if bio and re.search(r'(https?://|www\.|t\.me/|@[a-zA-Z0-9_]+)', bio): bot.delete_message(chat_id, message.message_id); bot.reply_to(message, f"⚠️ **Link in Bio!**"); return 
        except: pass
    if st.get('profanity') and not is_immune and text_content:
        is_profane = False
        words = re.findall(r'\b\w+\b', text_content.lower())
        if any(word in BAD_WORDS for word in words): is_profane = True
        if not is_profane and check_ai_profanity(text_content): is_profane = True
        if is_profane:
            try:
                bot.delete_message(chat_id, message.message_id)
                if st.get('warningbans'):
                    db_key = {"chat_id": chat_id, "user_id": user_id}
                    w = warns_col.find_one(db_key); count = w['count'] + 1 if w else 1
                    if count >= 3: bot.ban_chat_member(chat_id, user_id); warns_col.delete_one(db_key); bot.send_message(chat_id, f"🚫 **Banned:** (Toxic 3/3)", parse_mode='Markdown')
                    else: warns_col.update_one(db_key, {"$set": {"count": count}}, upsert=True); bot.send_message(chat_id, f"⚠️ **Warn:** ({count}/3)", parse_mode='Markdown')
                else: msg = bot.send_message(chat_id, f"⚠️ Language!"); time.sleep(3); bot.delete_message(chat_id, msg.message_id)
                return
            except: pass

    if not is_immune:
        to_del = False
        if st.get('blacklist_active') and st.get('blacklist'):
            triggered = False
            if message.content_type == 'sticker' and message.sticker.set_name:
                if f'"{message.sticker.set_name}"' in st['blacklist']: triggered = True
            if text_content:
                for bad in st['blacklist']:
                    if bad.startswith('"') and bad.endswith('"'):
                        if text_content == bad.strip('"'): triggered = True; break
                    else:
                        if bad in text_content: triggered = True; break
            if triggered and st.get('whitelist'):
                for good in st['whitelist']:
                    if good in text_content: triggered = False; break
            if triggered: to_del = True
        if not to_del:
            if st.get('nocontacts') and message.contact: to_del = True
            elif st.get('nolocations') and message.location: to_del = True
            elif st.get('noforwards') and (message.forward_date or message.forward_from or message.forward_from_chat): to_del = True
            elif st.get('nohashtags') and '#' in text_content: to_del = True
            elif st.get('nolinks'):
                if message.reply_markup: to_del = True
                elif message.entities or message.caption_entities:
                    ents = message.entities or message.caption_entities
                    for e in ents:
                        if e.type in ['url', 'text_link', 'mention']: to_del = True; break
        if to_del:
            try: bot.delete_message(chat_id, message.message_id); return
            except: pass

    if message.content_type in ['photo', 'video', 'document', 'audio', 'voice', 'sticker', 'animation']:
        if st.get('delay') and st['delay'] > 0:
            if not is_auth:
                t = time.time() + (st['delay'] * 60)
                pending_delete_col.insert_one({"chat_id": chat_id, "msg_id": message.message_id, "delete_at": t})

# --- ✏️ EDIT DELETER ---
@bot.edited_message_handler(func=lambda message: True)
def on_edit(message):
    if message.chat.type == 'private': return
    st = get_settings(message.chat.id)
    if not st.get('noedit'): return
    if message.from_user.id in st.get('auth_users', []): return
    try:
        warn = bot.reply_to(message, f"⚠️ **Edit Restricted!**"); t = time.time() + 60
        pending_delete_col.insert_one({"chat_id": message.chat.id, "msg_id": message.message_id, "delete_at": t})
        pending_delete_col.insert_one({"chat_id": message.chat.id, "msg_id": warn.message_id, "delete_at": t})
    except: pass

# --- UTILITIES ---
@bot.message_handler(commands=['setdelay'])
def set_delay(message):
    if not can_change_info(message.chat.id, message.from_user.id): return
    try: m = int(message.text.split()[1]); update_setting(message.chat.id, 'delay', m); bot.reply_to(message, f"✅ Media Delay: {m} mins.")
    except: pass

@bot.message_handler(commands=['resetlimits'])
def reset_limits(message):
    if message.chat.type == 'private': return
    if bot.get_chat_member(message.chat.id, message.from_user.id).status == 'creator':
        limits_col.delete_many({"chat_id": message.chat.id}); bot.reply_to(message, "✅ Limits Reset.")

@bot.message_handler(content_types=['new_chat_members', 'left_chat_member'])
def on_service(message):
    st = get_settings(message.chat.id)
    if st.get('noevents'):
        try: bot.delete_message(message.chat.id, message.message_id)
        except: pass
    if st.get('nobots') and message.new_chat_members:
        for member in message.new_chat_members:
            if member.is_bot:
                adder = message.from_user
                try:
                    adder_mem = bot.get_chat_member(message.chat.id, adder.id)
                    if not (adder_mem.status == 'creator' or (adder_mem.status == 'administrator' and adder_mem.can_promote_members)):
                        bot.kick_chat_member(message.chat.id, member.id)
                except: pass
    if message.content_type == 'left_chat_member':
        actor = message.from_user
        if actor.id == message.left_chat_member.id: return 
        allowed, count = check_admin_limit(message.chat.id, actor.id)
        if not allowed:
            try:
                bot.promote_chat_member(message.chat.id, actor.id, can_manage_chat=False)
                bot.restrict_chat_member(message.chat.id, actor.id, can_send_messages=True)
                safe_name = escape_md(actor.first_name)
                bot.send_message(message.chat.id, f"🚨 **ANTI-NUKE:** {safe_name} Demoted!", parse_mode='Markdown')
            except: pass

print("⚡ ULTIMATE BOT ONLINE (NSFW SIGHTENGINE ADDED)...")

# --- MOBILE NETWORK STABILITY ---
try:
    bot.delete_webhook() # Purge any zombie webhooks
except:
    pass

bot.infinity_polling(timeout=10, long_polling_timeout=5, skip_pending=True)
