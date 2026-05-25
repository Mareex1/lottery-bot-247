import os
import sqlite3
import json
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from telegram.error import TelegramError

# 🔧 CONFIG
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID", "@comejoin1a")
DB_FILE = "lottery_numbers.db"
CONFIG_FILE = "channel_ids.json"
PORT = int(os.getenv("PORT", 10000))

SHOW_RECENT = 6
BAR_LENGTH = 40

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
            data = json.load(f)
            print(f"📂 Loaded {len(data.get('grids', {}))} grid message IDs")
            return data
    print("📂 No existing message IDs found, will create new ones")
    return {"board": None, "grids": {}}

def save_ids(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"💾 Saved message IDs to {CONFIG_FILE}")

def edit_or_send(bot, msg_id, chat_id, text):
    """Edit existing message or send new one if edit fails"""
    if msg_id:
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, parse_mode="Markdown")
            print(f"✏️ Edited message {msg_id}")
            return msg_id
        except TelegramError as e:
            print(f"⚠️ Edit failed for {msg_id}: {e}")
            # Don't fall back to send - keep trying to edit same ID
    
    # Only send new if no msg_id exists
    try:
        msg = bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
        print(f"📩 Sent new message {msg.message_id}")
        return msg.message_id
    except Exception as e:
        print(f"❌ Failed to send message: {e}")
        return msg_id

def get_taken_numbers():
    """Helper to get all taken numbers"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT number FROM numbers WHERE taken=1")
    taken = set(r[0] for r in c.fetchall())
    conn.close()
    return taken

def get_recent_claims():
    """Helper to get recent claims"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT number, claimed_by FROM claims_log ORDER BY id DESC LIMIT ?", (SHOW_RECENT,))
    recent = c.fetchall()
    conn.close()
    return recent

def get_claimed_count():
    """Helper to get total claimed count"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM numbers WHERE taken=1")
    claimed = c.fetchone()[0]
    conn.close()
    return claimed

def update_board_only(bot):
    """Update ONLY the board message"""
    cfg = load_ids()
    claimed = get_claimed_count()
    recent = get_recent_claims()
    taken = get_taken_numbers()
    
    pct = round((claimed / 5000) * 100, 1)
    bar = "🟢" * int((claimed/5000)*BAR_LENGTH) + "⚪️" * (BAR_LENGTH - int((claimed/5000)*BAR_LENGTH))
    board = f"🎫 LIVE LOTTERY BOARD\n📊 Claimed: {claimed}/5000 ({pct}%)\n🟢 Available: {5000-claimed}\n{bar}\n\n🕒 Recent Claims:\n"
    board += "\n".join([f"• #{n} by {u}" for n, u in recent]) or "• No claims yet"
    
    if cfg.get("board"):
        cfg["board"] = edit_or_send(bot, cfg["board"], CHANNEL_ID, board)
        save_ids(cfg)

def update_grid_block(bot, start, end):
    """Update ONLY a specific 500-number block"""
    cfg = load_ids()
    taken = get_taken_numbers()
    
    key = f"{start}-{end}"
    items = [f"{n} {'✅' if n in taken else '❌'}" for n in range(start, end + 1)]
    text = f"📋 Numbers {start}-{end}:\n" + " ".join(items)
    
    if key in cfg.get("grids", {}):
        cfg["grids"][key] = edit_or_send(bot, cfg["grids"][key], CHANNEL_ID, text)
        save_ids(cfg)
        print(f"✅ Updated grid block {key}")
    else:
        print(f"⚠️ No message ID found for {key}, will create on full sync")

def sync_channel_full(bot):
    """Send all 11 messages ONCE (only on startup)"""
    cfg = load_ids()
    claimed = get_claimed_count()
    recent = get_recent_claims()
    taken = get_taken_numbers()

    # Update or create Board
    pct = round((claimed / 5000) * 100, 1)
    bar = "🟢" * int((claimed/5000)*BAR_LENGTH) + "⚪️" * (BAR_LENGTH - int((claimed/5000)*BAR_LENGTH))
    board = f"🎫 LIVE LOTTERY BOARD\n📊 Claimed: {claimed}/5000 ({pct}%)\n🟢 Available: {5000-claimed}\n{bar}\n\n🕒 Recent Claims:\n"
    board += "\n".join([f"• #{n} by {u}" for n, u in recent]) or "• No claims yet"
    
    cfg["board"] = edit_or_send(bot, cfg.get("board"), CHANNEL_ID, board)
    time.sleep(0.5)
    save_ids(cfg)

    # Update or create 10 Grid blocks
    for start in range(1, 5001, 500):
        end = start + 499
        key = f"{start}-{end}"
        items = [f"{n} {'✅' if n in taken else '❌'}" for n in range(start, end + 1)]
        text = f"📋 Numbers {start}-{end}:\n" + " ".join(items)
        
        cfg["grids"][key] = edit_or_send(bot, cfg["grids"].get(key), CHANNEL_ID, text)
        save_ids(cfg)  # Save after each block
        time.sleep(0.5)  # Avoid rate limits
    
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
        "• `/stats` → View summary\n"
        "• `/reset` → Clear all & start fresh\n\n"
        "📌 Channel updates automatically!"
    )

def get_cmd(update: Update, context: CallbackContext):
    start, end, err = parse_range(context.args)
    if err:
        update.message.reply_text(err, parse_mode="Markdown")
        return

    user = update.message.from_user.username or f"User{update.message.from_user.id}"
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
    
    # Update ONLY affected grid block + board
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
    
    # Update ONLY affected grid block + board
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
    # Full sync after reset
    sync_channel_full(context.bot)

def stats_cmd(update: Update, context: CallbackContext):
    conn = sqlite3.connect(DB_FILE)
    taken = conn.cursor().execute("SELECT COUNT(*) FROM numbers WHERE taken=1").fetchone()[0]
    conn.close()
    update.message.reply_text(f"📊 {taken}/5000 claimed | 🟢 {5000-taken} left", parse_mode="Markdown")

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
    
    print("🔄 Initial channel sync (creates 11 messages)...")
    sync_channel_full(updater.bot)
    print("✅ Bot is ready and listening!")
    
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
