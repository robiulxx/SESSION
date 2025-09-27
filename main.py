import os
import asyncio
from threading import Thread
from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import (
    SessionPasswordNeeded, PhoneCodeInvalid, PasswordHashInvalid, PhoneNumberInvalid,
    FloodWait
)
from telethon.sessions import StringSession
from telethon.sync import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError, PasswordHashInvalidError, PhoneNumberInvalidError,
    FloodWaitError
)

# ------------------------------
# ENV VARIABLES
# ------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH")

# ------------------------------
# Pyrogram Bot Client
# ------------------------------
bot = Client("session_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ------------------------------
# User session tracking
# ------------------------------
user_steps = {}       # {user_id: step_name}
user_temp_data = {}   # {user_id: temp_client, sent_code_info, session_type, phone_number, etc.}

# ------------------------------
# Cleanup function
# ------------------------------
def cleanup_user(user_id):
    if user_id in user_steps:
        del user_steps[user_id]
    if user_id in user_temp_data:
        temp_client = user_temp_data[user_id].get("temp_client")
        if temp_client:
            asyncio.create_task(temp_client.disconnect())
        del user_temp_data[user_id]

# ------------------------------
# Timeout handler (5 min OTP expiry)
# ------------------------------
async def timeout_cleanup(user_id, delay=300):
    await asyncio.sleep(delay)
    if user_id in user_steps:
        await bot.send_message(user_id, "OTP expired। `/generate` দিয়ে আবার শুরু করুন।")
        cleanup_user(user_id)

# ------------------------------
# /start command
# ------------------------------
@bot.on_message(filters.command("start") & filters.private)
async def start_handler(_, message: Message):
    await message.reply_text(
        "হ্যালো! আমি একটি Telegram string session generator বট।\n\n"
        "আপনার Pyrogram বা Telethon সেশন তৈরি করতে `/generate` দিন।"
    )

# ------------------------------
# /generate command
# ------------------------------
@bot.on_message(filters.command("generate") & filters.private)
async def generate_handler(_, message: Message):
    user_id = message.from_user.id
    cleanup_user(user_id)

    await message.reply_text(
        "কোন লাইব্রেরির জন্য session তৈরি করতে চান?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Pyrogram", callback_data="gen_pyrogram")],
            [InlineKeyboardButton("Telethon", callback_data="gen_telethon")]
        ])
    )
    user_steps[user_id] = "choosing_session_type"

# ------------------------------
# /cancel command
# ------------------------------
@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_handler(_, message: Message):
    user_id = message.from_user.id
    cleanup_user(user_id)
    await message.reply_text("প্রক্রিয়া বাতিল করা হলো।")

# ------------------------------
# Callback query handler
# ------------------------------
@bot.on_callback_query(filters.regex("^gen_"))
async def callback_handler(client, query):
    user_id = query.from_user.id
    if user_id not in user_steps or user_steps[user_id] != "choosing_session_type":
        await query.answer("দয়া করে /generate দিয়ে আবার শুরু করুন।", show_alert=True)
        return

    session_type = query.data.split("_")[1]
    user_temp_data[user_id] = {"session_type": session_type}
    user_steps[user_id] = "waiting_for_phone_number"

    await query.message.delete()
    await client.send_message(user_id, f"{session_type.capitalize()} session-এর জন্য ফোন নাম্বার দিন (+880...)")

# ------------------------------
# Handle user input (phone/code/password)
# ------------------------------
@bot.on_message(filters.text & filters.private & ~filters.command(["start", "generate", "cancel"]))
async def handle_input(client, message: Message):
    user_id = message.from_user.id
    step = user_steps.get(user_id)

    if step == "waiting_for_phone_number":
        phone = message.text.strip()
        if not phone.startswith("+") or len(phone) < 10:
            await message.reply_text("ভুল ফোন নাম্বার। +880... ফরম্যাটে দিন।")
            return

        user_temp_data[user_id]["phone_number"] = phone
        user_steps[user_id] = "waiting_for_code"
        await send_code(user_id)

    elif step == "waiting_for_code":
        otp = message.text.strip()
        user_temp_data[user_id]["otp_code"] = otp
        user_steps[user_id] = "waiting_for_2fa_password"
        await sign_in_user(user_id)

    elif step == "waiting_for_password":
        password = message.text.strip()
        user_temp_data[user_id]["password"] = password
        await check_password(user_id)

    else:
        await message.reply_text("প্রক্রিয়া শুরু করতে `/generate` দিন।")

# ------------------------------
# Send OTP
# ------------------------------
async def send_code(user_id):
    data = user_temp_data[user_id]
    phone = data["phone_number"]
    session_type = data["session_type"]

    # create temporary client once
    if session_type == "pyrogram":
        temp_client = Client(":memory:", api_id=API_ID, api_hash=API_HASH)
    else:
        temp_client = TelegramClient(StringSession(), API_ID, API_HASH)

    await temp_client.connect()
    data["temp_client"] = temp_client

    try:
        if session_type == "pyrogram":
            sent = await temp_client.send_code(phone)
            data["sent_code_info"] = sent
        else:
            await temp_client.send_code_request(phone)

        await bot.send_message(user_id, "OTP পাঠানো হলো। 5 মিনিটের মধ্যে submit করুন।")
        asyncio.create_task(timeout_cleanup(user_id, 300))  # 5 min timeout

    except (PhoneNumberInvalid, PhoneNumberInvalidError):
        await bot.send_message(user_id, "ভুল ফোন নাম্বার। আবার /generate দিয়ে চেষ্টা করুন।")
        cleanup_user(user_id)
    except FloodWait as e:
        await bot.send_message(user_id, f"অনেক চেষ্টা করা হয়েছে। {e.value} সেকেন্ড পর চেষ্টা করুন।")
        cleanup_user(user_id)
    except Exception as e:
        await bot.send_message(user_id, f"OTP পাঠাতে সমস্যা: {e}")
        cleanup_user(user_id)

# ------------------------------
# Sign in user
# ------------------------------
async def sign_in_user(user_id):
    data = user_temp_data[user_id]
    phone = data["phone_number"]
    otp = data["otp_code"]
    session_type = data["session_type"]
    temp_client = data["temp_client"]

    try:
        if session_type == "pyrogram":
            await temp_client.sign_in(phone, data["sent_code_info"].phone_code_hash, otp)
            session_str = await temp_client.export_session_string()
        else:
            await temp_client.sign_in(phone, otp)
            session_str = temp_client.session.save()

        await bot.send_message(user_id, f"Session তৈরি হলো:\n\n```python\n{session_str}\n```")
    except (SessionPasswordNeeded, SessionPasswordNeededError):
        user_steps[user_id] = "waiting_for_password"
        await bot.send_message(user_id, "2FA password দিন।")
        return
    except (PhoneCodeInvalid, PhoneCodeInvalidError):
        await bot.send_message(user_id, "ভুল OTP! /generate দিয়ে আবার চেষ্টা করুন।")
    except Exception as e:
        await bot.send_message(user_id, f"Sign in error: {e}")
    finally:
        cleanup_user(user_id)

# ------------------------------
# Check 2FA password
# ------------------------------
async def check_password(user_id):
    data = user_temp_data[user_id]
    temp_client = data["temp_client"]
    password = data["password"]
    session_type = data["session_type"]

    try:
        if session_type == "pyrogram":
            await temp_client.check_password(password)
            session_str = await temp_client.export_session_string()
        else:
            await temp_client.sign_in(password=password)
            session_str = temp_client.session.save()

        await bot.send_message(user_id, f"Session তৈরি হলো:\n\n```python\n{session_str}\n```")
    except Exception as e:
        await bot.send_message(user_id, f"Password error: {e}")
    finally:
        cleanup_user(user_id)

# ------------------------------
# Flask Web Service for Render
# ------------------------------
app = Flask(__name__)
@app.route("/")
def home():
    return "Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

# ------------------------------
# Run bot and Flask together
# ------------------------------
def run_bot():
    bot.run()

if __name__ == "__main__":
    # Flask thread
    Thread(target=run_flask).start()
    # Bot run
    run_bot()
