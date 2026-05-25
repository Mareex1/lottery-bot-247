import os
import sqlite3
import json
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackContext, CallbackQueryHandler
from telegram.error import TelegramError

# 🔧 CONFIG
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID", "@comejoin1a")
DB_FILE = "lottery_numbers.db"
CONFIG_FILE = "channel_ids.json"
PORT = int(os.getenv("PORT", 10000))

SHOW_RECENT = 6
BAR_LENGTH = 40

#  Helper: Escape characters for Markdown
def safe_md(text):
    return str(text).replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("]", "\\]")

#  Database Init
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
        cursor.executemany("INSERT INTO numbers (number) VALUES (?)", [(i,) for i in range(1, 5001)])
    cursor.execute("DELETE FROM claims_log WHERE id <= (SELECT MAX(id) - 50 FROM claims_log)")
    conn.commit()
    conn.close()
    print("✅ Database initialized")

def load_ids():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {"board": None, "grids": {}}

def save_ids(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

def edit_or_send(bot, msg_id, chat_id, text):
    """Edit existing message or send new one if edit fails"""
    text = safe_md(text)
    if msg_id:
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, parse_mode="Markdown")
            return msg_id
        except TelegramError:
            pass  # Fall back to send if edit fails (e.g., message deleted)
    
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
    
    pct = round((claimed / 5000) * 100, 1)
    bar = "🟢" * int((claimed/5000)*BAR_LENGTH) + "⚪️" * (BAR_LENGTH - int((claimed/5000)*BAR_LENGTH))
    board = f"🎫 LIVE LOTTERY BOARD\n Claimed: {claimed}/5000 ({pct}%)\n🟢 Available: {5000-claimed}\n{bar}\n\n🕒 Recent Claims:\n"
    board += "\n".join([f"• #{n} by {safe_md(u)}" for n, u in recent]) or "• No claims yet"
    
    if cfg.get("board"):
        cfg["board"] = edit_or_send(bot, cfg["board"], CHANNEL_ID, board)
        save_ids(cfg)

def update_grid_block(bot, start, end):
    cfg = load_ids()
    taken = get_taken_numbers()
    
    key = f"{start}-{end}"
    items = [f"{n} {'✅' if n in taken else '❌'}" for n in range(start, end + 1)]
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

    pct = round((claimed / 5000) * 100, 1)
    bar = "🟢" * int((claimed/5000)*BAR_LENGTH) + "⚪️" * (BAR_LENGTH - int((claimed/5000)*BAR_LENGTH))
    board = f"🎫 LIVE LOTTERY BOARD\n📊 Claimed: {claimed}/5000 ({pct}%)\n Available: {5000-claimed}\n{bar}\n\n🕒 Recent Claims:\n"
    board += "\n".join([f"• #{n} by {safe_md(u)}" for n, u in recent]) or "• No claims yet"
    
    cfg["board"] = edit_or_send(bot, cfg.get("board"), CHANNEL_ID, board)
    time.sleep(0.5)
    save_ids(cfg)

    for start in range(1, 5001, 500):
        end = start + 499
        key = f"{start}-{end}"
        items = [f"{n} {'✅' if n in taken else '❌'}" for n in range(start, end + 1)]
        text = f" Numbers {start}-{end}:\n" + " ".join(items)
        
        cfg["grids"][key] = edit_or_send(bot, cfg["grids"].get(key), CHANNEL_ID, text)
        save_ids(cfg)
        time.sleep(0.5)
    print("✅ Full channel sync complete - 11 messages created/updated")

#  Parse single number or range
def parse_range(args):
    if not args:
        return None, None, "❌ Usage: `/get 55` or `/get 30-300`"
    text = args[0].replace(" ", "")
    if '-' in text:
        parts = text.split('-')
        if len(parts) != 2:
            return None, None, "❌ Invalid range. Use: 30-300"
        try:
            start, end = int(parts[0]), int(parts[1])
        except ValueError:
            return None, None, "❌ Numbers must be integers."
        if start > end:
            start, end = end, start
        if not (1 <= start <= 5000 and 1 <= end <= 5000):
            return None, None, "❌ Range must be within 1-5000."
        return start, end, None
    else:
        try:
            num = int(text)
        except ValueError:
            return None, None, "❌ Invalid number."
        if not (1 <= num <= 5000):
            return None, None, "❌ Number must be 1-5000."
        return num, num, None

# 🎬 Commands
def start_cmd(update: Update, context: CallbackContext):
    update.message.reply_text(
        "🎫 Welcome to the Lottery Bot!\n\n"
        "• `/get <num>` or `/get <start>-<end>` → Select numbers\n"
        "• `/un <num>` or `/un <start>-<end>` → Release numbers\n"
        "• `/check <num>` → Interactive status with buttons\n"
        "• `/reset` → Clear all & start fresh\n"
        "• `/stats` → View summary\n\n"
        "📌 Channel updates automatically!"
    )

def get_cmd(update: Update, context: CallbackContext):
    start, end, err = parse_range(context.args)
    if err:
        update.message.reply_text(err, parse_mode="Markdown")
        return

    user = safe_md(update.message.from_user.username or f"User{update.message.from_user.id}")
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("BEGIN TRANSACTION")
    
    claimed = []
    already = []
    for n in range(start, end + 1):
        c.execute("UPDATE numbers SET taken=1, claimed_by=?, claimed_at=datetime('now') WHERE number=? AND taken=0", (user, n))
        if c.rowcount > 0:
            claimed.append(n)
            c.execute("INSERT INTO claims_log (number, claimed_by, claimed_at) VALUES (?, ?, datetime('now'))", (n, user))
        else:
            already.append(n)
    conn.commit()
    conn.close()

    msg = f"✅ Selected {len(claimed)} number(s)!"
    if already:
        msg += f"\n❌ {len(already)} already taken."
    update.message.reply_text(msg, parse_mode="Markdown")
    
    block_start = ((start - 1) // 500) * 500 + 1
    block_end = block_start + 499
    update_grid_block(context.bot, block_start, block_end)
    time.sleep(0.5)
    update_board_only(context.bot)

def un_cmd(update: Update, context: CallbackContext):
    start, end, err = parse_range(context.args)
    if err:
        update.message.reply_text(err, parse_mode="Markdown")
        return

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("BEGIN TRANSACTION")
    
    released = []
    not_taken = []
    for n in range(start, end + 1):
        c.execute("UPDATE numbers SET taken=0, claimed_by=NULL, claimed_at=NULL WHERE number=? AND taken=1")
        if c.rowcount > 0:
            released.append(n)
        else:
            not_taken.append(n)
    conn.commit()
    conn.close()

    msg = f"🔓 Released {len(released)} number(s)!"
    if not_taken:
        msg += f"\nℹ️ {len(not_taken)} were already available."
    update.message.reply_text(msg, parse_mode="Markdown")
    
    block_start = ((start - 1) // 500) * 500 + 1
    block_end = block_start + 499
    update_grid_block(context.bot, block_start, block_end)
    time.sleep(0.5)
    update_board_only(context.bot)

def reset_cmd(update: Update, context: CallbackContext):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE numbers SET taken=0, claimed_by=NULL, claimed_at=NULL")
    c.execute("DELETE FROM claims_log")
    conn.commit()
    conn.close()
    
    update.message.reply_text("⚠️ All data cleared! Starting fresh.")
    sync_channel_full(context.bot)

def stats_cmd(update: Update, context: CallbackContext):
    conn = sqlite3.connect(DB_FILE)
    taken = conn.cursor().execute("SELECT COUNT(*) FROM numbers WHERE taken=1").fetchone()[0]
    conn.close()
    update.message.reply_text(f"📊 {taken}/5000 claimed | 🟢 {5000-taken} left", parse_mode="Markdown")

#  Interactive Check Command with Buttons
def check_cmd(update: Update, context: CallbackContext):
    start, end, err = parse_range(context.args)
    if err:
        update.message.reply_text(err, parse_mode="Markdown")
        return
    
    if start != end:
        update.message.reply_text("Use `/get` for ranges. Use `/check` for a single number to see buttons.", parse_mode="Markdown")
        return

    num = start
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT taken, claimed_by FROM numbers WHERE number=?", (num,))
    row = c.fetchone()
    conn.close()

    is_taken = row and row[0] == 1
    owner = row[1] if is_taken else None
    user = update.message.from_user.username or f"User{update.message.from_user.id}"

    if is_taken:
        text = f"Number **#{num}** is:\n✅ Taken by {safe_md(owner)}"
        if owner == user:
            keyboard = [[InlineKeyboardButton(" Release", callback_data=f"un_{num}")]]
        else:
            keyboard = [[]]
    else:
        text = f"Number **#{num}** is:\n❌ Available"
        keyboard = [[InlineKeyboardButton("✅ Claim", callback_data=f"claim_{num}")]]

    # Always add Refresh button
    keyboard[0].append(InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{num}"))
    markup = InlineKeyboardMarkup(keyboard)

    update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")

# 🆕 Button Callback Handler
def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    
    data = query.data
    parts = data.split("_")
    action = parts[0]
    num = int(parts[1])
    
    user = safe_md(query.from_user.username or f"User{query.from_user.id}")
    
    # Fetch current status
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT taken, claimed_by FROM numbers WHERE number=?", (num,))
    row = c.fetchone()
    is_taken = row and row[0] == 1
    owner = row[1] if is_taken else None
    
    new_text = ""
    new_keyboard = []

    try:
        if action == "refresh":
            # Just update status, no DB change
            pass
        
        elif action == "claim":
            if is_taken:
                new_text = f"❌ Number **#{num}** was just taken by {safe_md(owner)}!"
                new_keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{num}")]]
                query.edit_message_text(text=new_text, reply_markup=InlineKeyboardMarkup(new_keyboard), parse_mode="Markdown")
                return
            
            # Claim it
            c.execute("UPDATE numbers SET taken=1, claimed_by=?, claimed_at=datetime('now') WHERE number=? AND taken=0", (user, num))
            if c.rowcount > 0:
                c.execute("INSERT INTO claims_log (number, claimed_by, claimed_at) VALUES (?, ?, datetime('now'))", (num, user))
                conn.commit()
                new_text = f"✅ You claimed **#{num}**!"
                new_keyboard = [
                    [InlineKeyboardButton("🔓 Release", callback_data=f"un_{num}"),
                     InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{num}")]
                ]
                # Update Channel
                block_start = ((num - 1) // 500) * 500 + 1
                update_grid_block(context.bot, block_start, block_start + 499)
                time.sleep(0.5)
                update_board_only(context.bot)
            else:
                new_text = f"❌ Number **#{num}** is already taken!"
                new_keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{num}")]]

        elif action == "un":
            if not is_taken or owner != user:
                new_text = f"ℹ️ You don't own **#{num}** anymore."
                new_keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{num}")]]
                query.edit_message_text(text=new_text, reply_markup=InlineKeyboardMarkup(new_keyboard), parse_mode="Markdown")
                return
            
            # Release it
            c.execute("UPDATE numbers SET taken=0, claimed_by=NULL, claimed_at=NULL WHERE number=? AND taken=1", (num,))
            if c.rowcount > 0:
                conn.commit()
                new_text = f"🔓 Released **#{num}**!"
                new_keyboard = [
                    [InlineKeyboardButton("✅ Claim", callback_data=f"claim_{num}"),
                     InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{num}")]
                ]
                # Update Channel
                block_start = ((num - 1) // 500) * 500 + 1
                update_grid_block(context.bot, block_start, block_start + 499)
                time.sleep(0.5)
                update_board_only(context.bot)
            else:
                new_text = f"ℹ️ Number **#{num}** is already available."
                new_keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{num}")]]

        # If we reached here, we need to update the message
        if not new_text:
            # Refresh logic: just rebuild status
            if is_taken:
                new_text = f"Number **#{num}** is:\n✅ Taken by {safe_md(owner)}"
                if owner == user:
                    new_keyboard = [[InlineKeyboardButton("🔓 Release", callback_data=f"un_{num}"), InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{num}")]]
                else:
                    new_keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{num}")]]
            else:
                new_text = f"Number **#{num}** is:\n❌ Available"
                new_keyboard = [[InlineKeyboardButton("✅ Claim", callback_data=f"claim_{num}"), InlineKeyboardButton(" Refresh", callback_data=f"refresh_{num}")]]

        query.edit_message_text(text=new_text, reply_markup=InlineKeyboardMarkup(new_keyboard), parse_mode="Markdown")

    except TelegramError as e:
        if "Message is not modified" in str(e):
            pass
        else:
            raise
    finally:
        conn.close()

# 🌐 Keep-alive server
class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is online")
    def log_message(self, format, *args): pass

def run_keepalive():
    server = HTTPServer(('0.0.0.0', PORT), KeepAliveHandler)
    server.serve_forever()

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
    dp.add_handler(CommandHandler("stats", stats_cmd))
    dp.add_handler(CommandHandler("check", check_cmd))
    dp.add_handler(CallbackQueryHandler(button_handler))
    
    print("🔄 Initial channel sync (creates 11 messages)...")
    sync_channel_full(updater.bot)
    print("✅ Bot is ready and listening!")
    
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
