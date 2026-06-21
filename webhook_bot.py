import os
import sqlite3
import json
import threading
import time
import random
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackContext, CallbackQueryHandler
from telegram.error import TelegramError

#  CONFIG
BOT_TOKEN = os.getenv("BOT_TOKEN")
# Hardcoded to prevent hosting platform environment variables from overriding it
CHANNEL_ID = "@emun2ti" 
DB_FILE = "lottery_numbers.db"
CONFIG_FILE = "channel_ids.json"
PORT = int(os.getenv("PORT", 10000))

SHOW_RECENT = 6
BAR_LENGTH = 40
TOTAL_NUMBERS = 3000  # Updated to 3000

def safe_md(text):
    return str(text).replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("]", "\\]").replace("`", "\\`")

def init_database():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""CREATE TABLE IF NOT EXISTS numbers (
        number INTEGER PRIMARY KEY, taken INTEGER DEFAULT 0, claimed_by TEXT, claimed_at TEXT
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS claims_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, number INTEGER, claimed_by TEXT, claimed_at TEXT
    )""")
    cursor.execute("SELECT COUNT(*) FROM numbers")
    if cursor.fetchone()[0] == 0:
        cursor.executemany("INSERT INTO numbers (number) VALUES (?)", [(i,) for i in range(1, TOTAL_NUMBERS + 1)])
    cursor.execute("DELETE FROM claims_log WHERE id <= (SELECT MAX(id) - 50 FROM claims_log)")
    conn.commit()
    conn.close()
    print(f"✅ Database initialized with {TOTAL_NUMBERS} numbers")

def load_ids():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {"board": None, "grids": {}}

def save_ids(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

def edit_or_send(bot, msg_id, chat_id, text):
    if msg_id:
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, parse_mode="Markdown")
            return msg_id
        except TelegramError:
            pass
    try:
        msg = bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
        return msg.message_id
    except Exception:
        return msg_id

def get_taken_numbers():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT number FROM numbers WHERE taken=1")
    taken = set(r[0] for r in c.fetchall())
    conn.close()
    return taken

def get_recent_claims():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT number, claimed_by FROM claims_log ORDER BY id DESC LIMIT ?", (SHOW_RECENT,))
    recent = c.fetchall()
    conn.close()
    return recent

def get_claimed_count():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM numbers WHERE taken=1")
    claimed = c.fetchone()[0]
    conn.close()
    return claimed

def update_board_only(bot):
    cfg = load_ids()
    claimed = get_claimed_count()
    recent = get_recent_claims()
    taken = get_taken_numbers()
    
    pct = round((claimed / TOTAL_NUMBERS) * 100, 1)
    bar = "🟢" * int((claimed/TOTAL_NUMBERS)*BAR_LENGTH) + "⚪️" * (BAR_LENGTH - int((claimed/TOTAL_NUMBERS)*BAR_LENGTH))
    board = f"🎫 LIVE LOTTERY BOARD\n📊 Claimed: {claimed}/{TOTAL_NUMBERS} ({pct}%)\n🟢 Available: {TOTAL_NUMBERS-claimed}\n{bar}\n\n🕒 Recent Claims:\n"
    board += "\n".join([f"• #{n} by {safe_md(u)}" for n, u in recent]) or "• No claims yet"
    
    if cfg.get("board"):
        cfg["board"] = edit_or_send(bot, cfg["board"], CHANNEL_ID, board)
        save_ids(cfg)

def update_grid_block(bot, start, end):
    cfg = load_ids()
    taken = get_taken_numbers()
    key = f"{start}-{end}"
    # ✅ Changed to ✅ for marked and 🔲 for unmarked
    items = [f"{n} {'✅' if n in taken else '🔲'}" for n in range(start, end + 1)]
    text = f"📋 Numbers {start}-{end}:\n" + " ".join(items)
    
    if key in cfg.get("grids", {}):
        cfg["grids"][key] = edit_or_send(bot, cfg["grids"][key], CHANNEL_ID, text)
        save_ids(cfg)
        print(f"✅ Updated grid block {key}")

def sync_channel_full(bot):
    cfg = load_ids()
    claimed = get_claimed_count()
    recent = get_recent_claims()
    taken = get_taken_numbers()

    # Reduced block size to 400 to prevent "Message is too long" errors with 4-digit numbers
    for start in range(1, TOTAL_NUMBERS + 1, 400):
        end = min(start + 399, TOTAL_NUMBERS)
        key = f"{start}-{end}"
        # ✅ Changed to ✅ for marked and 🔲 for unmarked
        items = [f"{n} {'✅' if n in taken else '🔲'}" for n in range(start, end + 1)]
        text = f"📋 Numbers {start}-{end}:\n" + " ".join(items)
        
        cfg["grids"][key] = edit_or_send(bot, cfg["grids"].get(key), CHANNEL_ID, text)
        save_ids(cfg)
        time.sleep(0.5)

    pct = round((claimed / TOTAL_NUMBERS) * 100, 1)
    bar = "🟢" * int((claimed/TOTAL_NUMBERS)*BAR_LENGTH) + "⚪️" * (BAR_LENGTH - int((claimed/TOTAL_NUMBERS)*BAR_LENGTH))
    board = f"🎫 LIVE LOTTERY BOARD\n📊 Claimed: {claimed}/{TOTAL_NUMBERS} ({pct}%)\n🟢 Available: {TOTAL_NUMBERS-claimed}\n{bar}\n\n🕒 Recent Claims:\n"
    board += "\n".join([f"• #{n} by {safe_md(u)}" for n, u in recent]) or "• No claims yet"
    
    cfg["board"] = edit_or_send(bot, cfg.get("board"), CHANNEL_ID, board)
    time.sleep(0.5)
    save_ids(cfg)
    print(f"✅ Full channel sync complete - {TOTAL_NUMBERS} numbers")

def delete_channel_messages(bot):
    cfg = load_ids()
    deleted = 0
    if cfg.get("board"):
        try:
            bot.delete_message(chat_id=CHANNEL_ID, message_id=cfg["board"])
            deleted += 1
            time.sleep(0.3)
        except Exception as e: print(f"⚠️ Board delete failed: {e}")
    for key, msg_id in cfg.get("grids", {}).items():
        try:
            bot.delete_message(chat_id=CHANNEL_ID, message_id=msg_id)
            deleted += 1
            time.sleep(0.3)
        except Exception as e: print(f"⚠️ Grid {key} delete failed: {e}")
    save_ids({"board": None, "grids": {}})
    print(f"✅ Deleted {deleted} old bot messages")

def parse_range(args):
    if not args:
        return None, None, "❌ Usage: `/get 55` or `/get 30-300`"
    text = args[0].replace(" ", "")
    if '-' in text:
        parts = text.split('-')
        if len(parts) != 2: return None, None, "❌ Invalid range. Use: 30-300"
        try: start, end = int(parts[0]), int(parts[1])
        except ValueError: return None, None, "❌ Numbers must be integers."
        if start > end: start, end = end, start
        if not (1 <= start <= TOTAL_NUMBERS and 1 <= end <= TOTAL_NUMBERS): return None, None, f"❌ Range must be within 1-{TOTAL_NUMBERS}."
        return start, end, None
    else:
        try: num = int(text)
        except ValueError: return None, None, "❌ Invalid number."
        if not (1 <= num <= TOTAL_NUMBERS): return None, None, f"❌ Number must be 1-{TOTAL_NUMBERS}."
        return num, num, None

def start_cmd(update: Update, context: CallbackContext):
    conn = sqlite3.connect(DB_FILE)
    taken = conn.cursor().execute("SELECT COUNT(*) FROM numbers WHERE taken=1").fetchone()[0]
    conn.close()
    
    welcome_text = (
        "🎉 **WELCOME TO THE LOTTERY BOT!** \n\n"
        "📊 **Current Status:**\n"
        f"• Numbers claimed: {taken}/{TOTAL_NUMBERS}\n"
        f"• Available: {TOTAL_NUMBERS-taken}\n\n"
        "🎯 **How to Play:**\n"
        "• `/get 42` or `/get 100-200` → Claim numbers\n"
        "• `/check 42` → Interactive buttons\n"
        "• `/un 42` or `/un 100-200` → Release numbers\n\n"
        "⚙️ **Tools:**\n"
        "• `/refresh` → Rebuild channel grid (preserves data)\n"
        "• `/reset` → Wipe DB & rebuild grid\n"
        "• `/draw` → Pick 3 random winners\n"
        "• `/stats` → View summary\n"
        "• `/ping` → Check uptime\n\n"
        "📌 **Check the channel** for the live number grid!"
    )
    
    update.message.reply_text(welcome_text, parse_mode="Markdown")

def ping_cmd(update: Update, context: CallbackContext):
    update.message.reply_text(f"✅ Bot is alive! Uptime: {time.strftime('%Y-%m-%d %H:%M:%S')}")

def refresh_cmd(update: Update, context: CallbackContext):
    update.message.reply_text("🔄 Refreshing channel grid...")
    delete_channel_messages(context.bot)
    time.sleep(1.5)
    update.message.reply_text("✅ Posting updated grid...")
    sync_channel_full(context.bot)
    update.message.reply_text("✅ Channel grid refreshed! All claims preserved.")

def get_cmd(update: Update, context: CallbackContext):
    start, end, err = parse_range(context.args)
    if err: update.message.reply_text(err, parse_mode="Markdown"); return
    user = safe_md(update.message.from_user.username or f"User{update.message.from_user.id}")
    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    c.execute("BEGIN TRANSACTION")
    claimed, already = [], []
    for n in range(start, end + 1):
        c.execute("UPDATE numbers SET taken=1, claimed_by=?, claimed_at=datetime('now') WHERE number=? AND taken=0", (user, n))
        if c.rowcount > 0:
            claimed.append(n)
            c.execute("INSERT INTO claims_log (number, claimed_by, claimed_at) VALUES (?, ?, datetime('now'))", (n, user))
        else: already.append(n)
    conn.commit(); conn.close()
    msg = f"✅ Selected {len(claimed)} number(s)!"
    if already: msg += f"\n❌ {len(already)} already taken."
    update.message.reply_text(msg, parse_mode="Markdown")
    
    start_block = ((start - 1) // 400) * 400 + 1
    end_block = ((end - 1) // 400) * 400 + 1
    
    for block_start in range(start_block, end_block + 1, 400):
        block_end = min(block_start + 399, TOTAL_NUMBERS)
        update_grid_block(context.bot, block_start, block_end)
        time.sleep(0.5)
    
    update_board_only(context.bot)

def un_cmd(update: Update, context: CallbackContext):
    start, end, err = parse_range(context.args)
    if err: update.message.reply_text(err, parse_mode="Markdown"); return
    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    c.execute("BEGIN TRANSACTION")
    released, not_taken = [], []
    for n in range(start, end + 1):
        c.execute("UPDATE numbers SET taken=0, claimed_by=NULL, claimed_at=NULL WHERE number=? AND taken=1", (n,))
        if c.rowcount > 0: released.append(n)
        else: not_taken.append(n)
    conn.commit(); conn.close()
    msg = f"🔓 Released {len(released)} number(s)!"
    if not_taken: msg += f"\nℹ️ {len(not_taken)} were already available."
    update.message.reply_text(msg, parse_mode="Markdown")
    
    start_block = ((start - 1) // 400) * 400 + 1
    end_block = ((end - 1) // 400) * 400 + 1
    
    for block_start in range(start_block, end_block + 1, 400):
        block_end = min(block_start + 399, TOTAL_NUMBERS)
        update_grid_block(context.bot, block_start, block_end)
        time.sleep(0.5)
    
    update_board_only(context.bot)

def reset_cmd(update: Update, context: CallbackContext):
    update.message.reply_text("🧹 Deleting old channel messages...")
    delete_channel_messages(context.bot); time.sleep(1.5)
    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    c.execute("UPDATE numbers SET taken=0, claimed_by=NULL, claimed_at=NULL")
    c.execute("DELETE FROM claims_log")
    conn.commit(); conn.close()
    update.message.reply_text("✅ Database cleared. Posting fresh grid...")
    sync_channel_full(context.bot)
    update.message.reply_text("🎉 Fresh grid posted! Check your channel.")

def stats_cmd(update: Update, context: CallbackContext):
    conn = sqlite3.connect(DB_FILE)
    taken = conn.cursor().execute("SELECT COUNT(*) FROM numbers WHERE taken=1").fetchone()[0]
    conn.close()
    update.message.reply_text(f"📊 {taken}/{TOTAL_NUMBERS} claimed | 🟢 {TOTAL_NUMBERS-taken} left", parse_mode="Markdown")

def draw_cmd(update: Update, context: CallbackContext):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT number FROM numbers WHERE taken=1")
    claimed = c.fetchall()
    conn.close()

    if not claimed:
        update.message.reply_text("❌ No numbers have been claimed yet! Ask users to `/get` numbers first.", parse_mode="Markdown")
        return

    anim_msg = update.message.reply_text("🎲 **DRAW IN PROGRESS...**\n\n🔄 Shuffling numbers...", parse_mode="Markdown")
    num_to_pick = min(3, len(claimed))
    
    time.sleep(1)
    anim_msg.edit_text("🎲 **DRAW IN PROGRESS...**\n\n📊 Total entries: {}\n⏳ Selecting winners...".format(len(claimed)), parse_mode="Markdown")
    
    time.sleep(1.5)
    anim_msg.edit_text("🎲 **DRAW IN PROGRESS...**\n\n📊 Total entries: {}\n🎯 Picking winner #1...".format(len(claimed)), parse_mode="Markdown")
    
    winners = random.sample(claimed, num_to_pick)
    time.sleep(2)
    
    text = "🎉 **FINAL DRAW RESULTS!**\n\n🎟️ Total claimed: {}/{}\n🏆 Winning Numbers ({}/3):\n\n".format(len(claimed), TOTAL_NUMBERS, num_to_pick)
    
    for i, (num,) in enumerate(winners, 1):
        text += "🥇 **#{} Place:** {}\n".format(i, num)
        if i < num_to_pick:
            anim_msg.edit_text(text + "\n⏳ Picking next winner...", parse_mode="Markdown")
            time.sleep(2)
    
    text += "\n✨ **Congratulations to the winners!** ✨"
    anim_msg.edit_text(text, parse_mode="Markdown")

def check_cmd(update: Update, context: CallbackContext):
    start, end, err = parse_range(context.args)
    if err: update.message.reply_text(err, parse_mode="Markdown"); return
    if start != end:
        update.message.reply_text("Use `/get` for ranges. Use `/check` for single numbers.", parse_mode="Markdown"); return
    num = start
    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    c.execute("SELECT taken, claimed_by FROM numbers WHERE number=?", (num,))
    row = c.fetchone(); conn.close()
    is_taken = row and row[0] == 1
    owner = row[1] if is_taken else None
    user = update.message.from_user.username or f"User{update.message.from_user.id}"
    if is_taken:
        text = f"Number **#{num}** is:\n✅ Taken by {safe_md(owner)}"
        keyboard = [[InlineKeyboardButton("🔓 Release", callback_data=f"un_{num}")]] if owner == user else [[]]
    else:
        text = f"Number **#{num}** is:\n🔲 Available"
        keyboard = [[InlineKeyboardButton("✅ Claim", callback_data=f"claim_{num}")]]
    keyboard[0].append(InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{num}"))
    update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    data = query.data
    action, num_str = data.split("_", 1)
    num = int(num_str)
    user = safe_md(query.from_user.username or f"User{query.from_user.id}")
    
    conn = sqlite3.connect(DB_FILE); c = conn.cursor()
    c.execute("SELECT taken, claimed_by FROM numbers WHERE number=?", (num,))
    row = c.fetchone()
    is_taken = row and row[0] == 1
    owner = row[1] if is_taken else None
    
    try:
        if action == "claim":
            if is_taken:
                query.edit_message_text(text=f"❌ #{num} was just taken by {safe_md(owner)}!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{num}")]]), parse_mode="Markdown"); return
            c.execute("UPDATE numbers SET taken=1, claimed_by=?, claimed_at=datetime('now') WHERE number=? AND taken=0", (user, num))
            if c.rowcount:
                c.execute("INSERT INTO claims_log (number, claimed_by, claimed_at) VALUES (?, ?, datetime('now'))", (num, user))
                conn.commit()
                new_kb = [[InlineKeyboardButton("🔓 Release", callback_data=f"un_{num}"), InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{num}")]]
                query.edit_message_text(text=f"✅ You claimed **#{num}**!", reply_markup=InlineKeyboardMarkup(new_kb), parse_mode="Markdown")
                block = ((num - 1) // 400) * 400 + 1
                update_grid_block(context.bot, block, min(block + 399, TOTAL_NUMBERS)); time.sleep(0.5); update_board_only(context.bot)
            else:
                query.edit_message_text(text=f"❌ #{num} is already taken!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{num}")]]), parse_mode="Markdown")
                
        elif action == "un":
            if not is_taken or owner != user:
                query.edit_message_text(text=f"⚠️ You don't own #{num} anymore.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{num}")]]), parse_mode="Markdown"); return
            c.execute("UPDATE numbers SET taken=0, claimed_by=NULL, claimed_at=NULL WHERE number=? AND taken=1", (num,))
            if c.rowcount:
                conn.commit()
                new_kb = [[InlineKeyboardButton("✅ Claim", callback_data=f"claim_{num}"), InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{num}")]]
                query.edit_message_text(text=f"🔓 Released **#{num}**!", reply_markup=InlineKeyboardMarkup(new_kb), parse_mode="Markdown")
                block = ((num - 1) // 400) * 400 + 1
                update_grid_block(context.bot, block, min(block + 399, TOTAL_NUMBERS)); time.sleep(0.5); update_board_only(context.bot)
            else:
                query.edit_message_text(text=f"ℹ️ #{num} is already available.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{num}")]]), parse_mode="Markdown")
                
        elif action == "refresh":
            if is_taken:
                text = f"Number **#{num}** is:\n✅ Taken by {safe_md(owner)}"
                kb = [[InlineKeyboardButton("🔓 Release", callback_data=f"un_{num}"), InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{num}")]] if owner == user else [[InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{num}")]]
            else:
                text = f"Number **#{num}** is:\n🔲 Available"
                kb = [[InlineKeyboardButton("✅ Claim", callback_data=f"claim_{num}"), InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{num}")]]
            query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            
    except TelegramError as e:
        if "Message is not modified" not in str(e): raise
    finally: conn.close()

class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"Bot is online")
    def log_message(self, format, *args): pass

def run_keepalive():
    HTTPServer(('0.0.0.0', PORT), KeepAliveHandler).serve_forever()

def main():
    init_database()
    threading.Thread(target=run_keepalive, daemon=True).start()
    print(f"🌐 Keep-alive server running on port {PORT}")
    
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start_cmd))
    dp.add_handler(CommandHandler("get", get_cmd))
    dp.add_handler(CommandHandler("un", un_cmd))
    dp.add_handler(CommandHandler("reset", reset_cmd))
    dp.add_handler(CommandHandler("refresh", refresh_cmd))
    dp.add_handler(CommandHandler("stats", stats_cmd))
    dp.add_handler(CommandHandler("check", check_cmd))
    dp.add_handler(CommandHandler("ping", ping_cmd))
    dp.add_handler(CommandHandler("draw", draw_cmd))
    dp.add_handler(CallbackQueryHandler(button_handler))
    
    print(f"🔄 Syncing channel ({TOTAL_NUMBERS} numbers, Grids → Board)...")
    sync_channel_full(updater.bot)
    print("✅ Bot ready!")
    updater.start_polling(); updater.idle()

if __name__ == '__main__':
    main()
