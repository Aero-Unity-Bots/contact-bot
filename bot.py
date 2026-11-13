import os
import time
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from threading import Thread

from flask import Flask
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, UserIsBlocked, InputUserDeactivated
from google import genai

# ==========================================
# CONFIGURATION (Set in Render Env Vars)
# ==========================================
API_ID = int(os.environ.get("API_ID", "12345678"))
API_HASH = os.environ.get("API_HASH", "your_api_hash_here")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token_here")
OWNER_ID = int(os.environ.get("OWNER_ID", "123456789"))  # Your numeric Telegram ID
MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://user:pass@cluster.mongodb.net/?retryWrites=true&w=majority")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "your_gemini_api_key_here")
UPDATES_CHANNEL_URL = os.environ.get("UPDATES_CHANNEL_URL", "https://t.me/Aero_Unity")  # Change to your channel/group
PORT = int(os.environ.get("PORT", 8080))

# Timezone: IST (Asia/Kolkata)
IST = timezone(timedelta(hours=5, minutes=30))

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ==========================================
# DATABASE & GEMINI SETUP
# ==========================================
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client["ContactRelayBot"]
users_col = db["users"]
msg_map_col = db["message_map"]
blocked_col = db["blocked_users"]
settings_col = db["settings"]

# Initialize Gemini Client
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# ==========================================
# FLASK KEEP-ALIVE SERVER (For Render 24/7)
# ==========================================
web_app = Flask(__name__)

@web_app.route("/")
def home():
    return {
        "status": "online",
        "service": "AI-Powered Contact Relay Node",
        "timestamp": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    }

def run_flask():
    web_app.run(host="0.0.0.0", port=PORT)

# ==========================================
# PYROGRAM BOT CLIENT
# ==========================================
app = Client(
    "contact_relay_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    parse_mode=enums.ParseMode.HTML
)

def get_ist_time() -> str:
    return datetime.now(IST).strftime("%I:%M %p • %d %b")

async def save_user(user):
    await users_col.update_one(
        {"user_id": user.id},
        {
            "$set": {
                "first_name": user.first_name,
                "username": user.username,
                "last_active": datetime.now(IST).isoformat()
            },
            "$setOnInsert": {
                "joined_at": datetime.now(IST).isoformat()
            }
        },
        upsert=True
    )

async def is_ai_enabled() -> bool:
    setting = await settings_col.find_one({"key": "ai_mode"})
    return setting.get("value", False) if setting else False

# ==========================================
# /START COMMAND
# ==========================================
@app.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    user = message.from_user
    
    # Check if user is blocked
    if await blocked_col.find_one({"user_id": user.id}):
        return

    await save_user(user)

    if user.id == OWNER_ID:
        total_users = await users_col.count_documents({})
        total_blocked = await blocked_col.count_documents({})
        ai_status = "🟢 Enabled" if await is_ai_enabled() else "🔴 Disabled"
        await message.reply_text(
            f"<b>⚡ Neural Relay Console • v3.1 Pro</b>\n\n"
            f"<blockquote>"
            f"<b>Status:</b> Active & Listening\n"
            f"<b>AI Auto-Responder:</b> {ai_status}\n"
            f"<b>Connected Users:</b> <code>{total_users}</code>\n"
            f"<b>Blocked Users:</b> <code>{total_blocked}</code>\n"
            f"</blockquote>\n\n"
            f"<i>Admin Commands:\n"
            f"• /ai on or /ai off - Toggle Gemini Auto-Responder\n"
            f"• /block (reply to user) - Block user\n"
            f"• /unblock &lt;user_id&gt; - Unblock user\n"
            f"• /stats - System telemetry\n"
            f"• /broadcast - Send message to all users</i>"
        )
        return

    welcome_text = (
        f"<b>✦ Direct Link Established</b> <code>[SECURE]</code>\n\n"
        f"Hello <b>{user.first_name}</b>, welcome to my direct contact portal.\n\n"
        f"<blockquote>"
        f"💬 <b>How this works:</b>\n"
        f"Send any text, photo, voice note, or file below. It will be routed directly to the owner.\n\n"
        f"🤖 <i>Note: An AI assistant may reply if the owner is away, but all messages are read by the owner.</i>"
        f"</blockquote>"
    )

    # Inline Keyboard with Updates Button
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Updates & Channels", url=UPDATES_CHANNEL_URL)]
    ])

    await message.reply_text(welcome_text, reply_markup=keyboard)

# ==========================================
# ADMIN: AI TOGGLE & BAN COMMANDS
# ==========================================
@app.on_message(filters.command("ai") & filters.user(OWNER_ID) & filters.private)
async def toggle_ai(client: Client, message: Message):
    args = message.command
    if len(args) < 2 or args[1].lower() not in ["on", "off"]:
        current = "ON" if await is_ai_enabled() else "OFF"
        await message.reply_text(f"⚠️ Usage: <code>/ai on</code> or <code>/ai off</code>\nCurrent AI Status: <b>{current}</b>")
        return

    state = args[1].lower() == "on"
    await settings_col.update_one(
        {"key": "ai_mode"},
        {"$set": {"value": state}},
        upsert=True
    )
    status_str = "🟢 Enabled" if state else "🔴 Disabled"
    await message.reply_text(f"<b>🤖 Gemini AI Auto-Responder is now {status_str}</b>")

@app.on_message(filters.command("block") & filters.user(OWNER_ID) & filters.private)
async def block_user(client: Client, message: Message):
    target_user_id = None
    if message.reply_to_message:
        replied_id = message.reply_to_message.id
        record = await msg_map_col.find_one({"owner_msg_id": replied_id})
        if record:
            target_user_id = record["user_id"]

    if not target_user_id and len(message.command) > 1:
        try:
            target_user_id = int(message.command[1])
        except ValueError:
            pass

    if not target_user_id:
        await message.reply_text("⚠️ <b>Reply to a user's forwarded message</b> with <code>/block</code> or provide their User ID.")
        return

    await blocked_col.update_one({"user_id": target_user_id}, {"$set": {"blocked_at": datetime.now(IST).isoformat()}}, upsert=True)
    await message.reply_text(f"🚫 <b>User <code>{target_user_id}</code> has been blocked.</b>")
    
    try:
        await client.send_message(target_user_id, "⚠️ <b>Notice:</b> You have been blocked from sending messages through this bot.")
    except Exception:
        pass

@app.on_message(filters.command("unblock") & filters.user(OWNER_ID) & filters.private)
async def unblock_user(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("⚠️ Usage: <code>/unblock &lt;user_id&gt;</code>")
        return
    try:
        target_user_id = int(message.command[1])
    except ValueError:
        await message.reply_text("❌ Invalid User ID.")
        return

    result = await blocked_col.delete_one({"user_id": target_user_id})
    if result.deleted_count > 0:
        await message.reply_text(f"✅ <b>User <code>{target_user_id}</code> has been unblocked.</b>")
    else:
        await message.reply_text(f"⚠️ User <code>{target_user_id}</code> was not found in the blocklist.")

@app.on_message(filters.command("stats") & filters.user(OWNER_ID) & filters.private)
async def stats_handler(client: Client, message: Message):
    total_users = await users_col.count_documents({})
    total_blocked = await blocked_col.count_documents({})
    total_msgs = await msg_map_col.count_documents({})
    ai_status = "Enabled" if await is_ai_enabled() else "Disabled"
    await message.reply_text(
        f"<b>📊 System Telemetry</b>\n\n"
        f"<blockquote>"
        f"• <b>Total Users:</b> <code>{total_users}</code>\n"
        f"• <b>Blocked Users:</b> <code>{total_blocked}</code>\n"
        f"• <b>Relayed Messages:</b> <code>{total_msgs}</code>\n"
        f"• <b>AI Auto-Responder:</b> <code>{ai_status}</code>\n"
        f"• <b>Server Time:</b> <code>{get_ist_time()} IST</code>"
        f"</blockquote>"
    )

@app.on_message(filters.command("broadcast") & filters.user(OWNER_ID) & filters.private)
async def broadcast_handler(client: Client, message: Message):
    if not message.reply_to_message:
        await message.reply_text("⚠️ <b>Reply to a message</b> with <code>/broadcast</code> to send it to all users.")
        return

    status_msg = await message.reply_text("⏳ <b>Initializing broadcast sequence...</b>")
    users = users_col.find({})
    sent, failed = 0, 0

    async for u in users:
        uid = u["user_id"]
        if uid == OWNER_ID or await blocked_col.find_one({"user_id": uid}):
            continue
        try:
            await message.reply_to_message.copy(chat_id=uid)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await status_msg.edit_text(
        f"<b>✅ Broadcast Complete</b>\n\n"
        f"<blockquote>"
        f"• <b>Delivered:</b> <code>{sent}</code>\n"
        f"• <b>Failed/Blocked:</b> <code>{failed}</code>"
        f"</blockquote>"
    )

# ==========================================
# USER -> OWNER RELAY & GEMINI AI AUTO-RESPONDER
# ==========================================
@app.on_message(filters.private & ~filters.user(OWNER_ID) & ~filters.command(["start"]))
async def user_to_owner(client: Client, message: Message):
    user = message.from_user

    # Drop if blocked
    if await blocked_col.find_one({"user_id": user.id}):
        return

    await save_user(user)

    username_str = f"@{user.username}" if user.username else "No Username"
    user_link = f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"

    # 1. Forward transmission header to Owner
    header_text = (
        f"<b>📥 Incoming Transmission</b> <code>[RELAY]</code>\n"
        f"<blockquote>"
        f"<b>From:</b> {user_link} ({username_str})\n"
        f"<b>User ID:</b> <code>{user.id}</code>\n"
        f"<b>Time:</b> <code>{get_ist_time()}</code>"
        f"</blockquote>\n"
        f"<i>↳ Reply to the message below to respond manually.</i>"
    )
    header_msg = await client.send_message(chat_id=OWNER_ID, text=header_text)

    # 2. Copy user message to Owner
    copied_msg = await message.copy(chat_id=OWNER_ID)

    # 3. Map messages in MongoDB
    await msg_map_col.insert_many([
        {"owner_msg_id": header_msg.id, "user_id": user.id, "user_msg_id": message.id},
        {"owner_msg_id": copied_msg.id, "user_id": user.id, "user_msg_id": message.id}
    ])

    # 4. Check if Gemini AI Auto-Responder is enabled
    if await is_ai_enabled() and message.text:
        try:
            prompt = (
                f"You are the intelligent virtual assistant for the owner of this Telegram bot. "
                f"A user named {user.first_name} just sent this message: '{message.text}'. "
                f"Provide a polite, helpful, concise response letting them know the owner has been notified and will reply personally soon. Keep it natural and professional."
            )
            response = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            ai_reply_text = response.text

            formatted_ai_reply = (
                f"<b>🤖 AI Assistant Auto-Reply</b>\n"
                f"<blockquote>{ai_reply_text}</blockquote>\n"
                f"<i>(The owner has been notified and can take over anytime)</i>"
            )
            await message.reply_text(formatted_ai_reply, quote=True)

            await client.send_message(
                chat_id=OWNER_ID,
                text=f"🤖 <i>Gemini auto-responded to {user_link}</i>"
            )
            return
        except Exception as e:
            logger.error(f"Gemini API Error: {e}")

    # Default acknowledgment if AI is off or message is non-text
    await message.reply_text(
        f"<blockquote>"
        f"✓ <b>Transmitted to Owner</b> • <code>{get_ist_time()}</code>\n"
        f"<i>You will be notified here once a reply is sent.</i>"
        f"</blockquote>",
        quote=True
    )

# ==========================================
# OWNER -> USER REPLY RELAY
# ==========================================
@app.on_message(filters.private & filters.user(OWNER_ID) & filters.reply)
async def owner_to_user(client: Client, message: Message):
    if message.text and message.text.startswith("/"):
        return

    replied_id = message.reply_to_message.id
    record = await msg_map_col.find_one({"owner_msg_id": replied_id})

    if not record:
        await message.reply_text("⚠️ <b>Target Not Found:</b> Make sure you are replying directly to a forwarded user message or header card.")
        return

    target_user_id = record["user_id"]
    original_user_msg_id = record.get("user_msg_id")

    try:
        if message.text:
            formatted_reply = (
                f"<b>⚡ Owner Response</b>\n"
                f"<blockquote>{message.text}</blockquote>\n"
                f"<i>Reply below to continue the conversation.</i>"
            )
            await client.send_message(
                chat_id=target_user_id,
                text=formatted_reply,
                reply_to_message_id=original_user_msg_id
            )
        else:
            await message.copy(
                chat_id=target_user_id,
                reply_to_message_id=original_user_msg_id
            )

        await message.reply_text(
            f"<blockquote>✅ <b>Delivered to</b> <code>{target_user_id}</code> • <code>{get_ist_time()}</code></blockquote>",
            quote=True
        )

    except UserIsBlocked:
        await message.reply_text("❌ <b>Delivery Failed:</b> The user has blocked the bot.")
    except Exception as e:
        await message.reply_text(f"❌ <b>Error:</b> <code>{str(e)}</code>")

# ==========================================
# STARTUP ENTRYPOINT
# ==========================================
if __name__ == "__main__":
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info(f"Flask server started on port {PORT}")

    logger.info("Starting Contact Relay Bot with Updates Button, Gemini AI & Block System...")
    app.run()
