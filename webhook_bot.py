import os
import sqlite3
import asyncio
import json
import threading
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from http.server import HTTPServer, BaseHTTPRequestHandler

# 🔧 CONFIG
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID", "@comejoin1a")
DB_FILE = "lottery_numbers.db"
CONFIG_FILE = "channel_ids.json"

db_lock = asyncio.Lock()
SHOW_RECENT = 6
BAR_LENGTH = 40

# 📦 Database Init
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
    print("✅ Database ready")

def load_ids():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {"board": None, "grids": {}}

def save_ids(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f)

async def edit_or_send(app, msg_id, chat_id, text):
    if msg_id:
        try:
            await app.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text)
            return msg_id
        except:
            pass
    msg = await app.bot.send_message(chat_id=chat_id, text=text)
    return msg.message_id

async def sync_channel_full(app):
    cfg = load_ids()
    async with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM numbers WHERE taken=1")
        claimed = c.fetchone()[0]
        c.execute("SELECT number, claimed_by FROM claims_log ORDER BY id DESC LIMIT ?", (SHOW_RECENT,))
        recent = c.fetchall()
        c.execute("SELECT number FROM numbers WHERE taken=1")
        taken = set(r[0] for r in c.fetchall())
        conn.close()

    pct = round((claimed / 5000) * 100, 1)
    bar = "🟢" * int((claimed/5000)*BAR_LENGTH) + "⚪️" * (BAR_LENGTH - int((claimed/5000)*BAR_LENGTH))
    board = f"🎫 LIVE LOTTERY BOARD\n📊 Claimed: {claimed}/5000 ({pct}%)\n🟢 Available: {5000-claimed}\n{bar}\n\n🕒 Recent Claims:\n"
    board += "\n".join([f"• #{n} by {u}" for n, u in recent]) or "• No claims yet"
    
    cfg["board"] = await edit_or_send(app, cfg.get("board"), CHANNEL_ID, board)
    await asyncio.sleep(0.4)
    save_ids(cfg)

    for start in range(1, 5001, 500):
        end = start + 499
        key = f"{start}-{end}"
        items = [f"{n} {'✅' if n in taken else '❌'}" for n in range(start, end + 1)]
        text = f"📋 Numbers {start}-{end}:\n" + " ".join(items)
        cfg["grids"][key] = await edit_or_send(app, cfg["grids"].get(key), CHANNEL_ID, text)
        save_ids(cfg)
        await asyncio.sleep(0.4)
    print("✅ Channel synced")

async def sync_channel_fast(app, claimed_num):
    cfg = load_ids()
    async with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM numbers WHERE taken=1")
        claimed = c.fetchone()[0]
        c.execute("SELECT number, claimed_by FROM claims_log ORDER BY id DESC LIMIT ?", (SHOW_RECENT,))
        recent = c.fetchall()
        c.execute("SELECT number FROM numbers WHERE taken=1")
        taken = set(r[0] for r in c.fetchall())
        conn.close()

    pct = round((claimed / 5000) * 100, 1)
    bar = "🟢" * int((claimed/5000)*BAR_LENGTH) + "⚪️" * (BAR_LENGTH - int((claimed/5000)*BAR_LENGTH))
    board = f"🎫 LIVE LOTTERY BOARD\n📊 Claimed: {claimed}/5000 ({pct}%)\n🟢 Available: {5000-claimed}\n{bar}\n\n🕒 Recent Claims:\n"
    board += "\n".join([f"• #{n} by {u}" for n, u in recent]) or "• No claims yet"
    
    cfg["board"] = await edit_or_send(app, cfg["board"], CHANNEL_ID, board)
    save_ids(cfg)
    await asyncio.sleep(0.4)

    start = ((claimed_num - 1) // 500) * 500 + 1
    end = start + 499
    key = f"{start}-{end}"
    items = [f"{n} {'✅' if n in taken else '❌'}" for n in range(start, end + 1)]
    text = f"📋 Numbers {start}-{end}:\n" + " ".join(items)
    cfg["grids"][key] = await edit_or_send(app, cfg["grids"].get(key), CHANNEL_ID, text)
    save_ids(cfg)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎫 Welcome! Type `/claim <1-5000>` to take a number.")

async def claim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Usage: `/claim 55`", parse_mode="Markdown"); return
    try:
        num = int(context.args[0])
        if not (1 <= num <= 5000):
            await update.message.reply_text("❌ Must be 1-5000."); return
        user = update.message.from_user.username or f"User{update.message.from_user.id}"
        async with db_lock:
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute("UPDATE numbers SET taken=1, claimed_by=?, claimed_at=datetime('now') WHERE number=? AND taken=0", (user, num))
            if c.rowcount == 0:
                c.execute("SELECT claimed_by FROM numbers WHERE number=?", (num,))
                row = c.fetchone()
                await update.message.reply_text(f"❌ #{num} already taken by {row[0] or 'unknown'}!")
                conn.close(); return
            c.execute("INSERT INTO claims_log (number, claimed_by, claimed_at) VALUES (?, ?, datetime('now'))", (num, user))
            conn.commit(); conn.close()
        await sync_channel_fast(context.application, num)
        await update.message.reply_text(f"✅ Claimed **#{num}**!", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Send a valid number.")

async def get_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: await update.message.reply_text("❌ `/get 55`", parse_mode="Markdown"); return
    try:
        num = int(context.args[0])
        if not (1 <= num <= 5000): await update.message.reply_text("❌ 1-5000 only."); return
        async with db_lock:
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute("SELECT taken, claimed_by FROM numbers WHERE number=?", (num,))
            row = c.fetchone(); conn.close()
        if row and row[0] == 1:
            await update.message.reply_text(f"#{num}: ✅ Taken by {row[1] or 'unknown'}", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"#{num}: ❌ Available", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Send a valid number.")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with db_lock:
        conn = sqlite3.connect(DB_FILE)
        taken = conn.cursor().execute("SELECT COUNT(*) FROM numbers WHERE taken=1").fetchone()[0]
        conn.close()
    await update.message.reply_text(f"📊 {taken}/5000 claimed | 🟢 {5000-taken} left", parse_mode="