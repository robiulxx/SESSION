import os
import asyncio
from threading import Thread
from flask import Flask
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
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

# ------------------------------
# Pyrogram Bot Client
# ------------------------------
bot = Client("session_bot", bot_token=BOT_TOKEN)

# ------------------------------
# User session tracking
# ------------------------------
user_steps = {}       # {user_id: step_name}
user_temp_data = {}   # {user_id: temp_client, sent_code_info, session_type, phone_number, api_id, api_hash}

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
async def start_handler(_, message):
    await message.reply_text(
        "নমস্কার! আমি একটি Telegram string session generator বট।\n\n"
        "আপনার Pyrogram বা Telethon session তৈরি করতে `/generate` দিন।"
    )

# ------------------------------
# /generate command
# ------------------------------
@bot.on_message(filters.command("generate") & filters.private)
async def generate_handler(_, message):
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
async def cancel_handler(_, message):
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
    user_steps[user_id] = "waiting_for_api_id"

    await query.message.delete()
    await client.send_message(user_id, f"{session_type.capitalize()} session-এর জন্য প্রথমে **API_ID** দিন:")

# ------------------------------
# Handle user input (API_ID, API_HASH, phone, OTP, 2FA)
# ------------------------------
@bot.on_message(filters.text & filters.private & ~filters.command(["start", "generate", "cancel"]))
async def handle_input(client, message):
    user_id = message.from_user.id
    step = user_steps.get(user_id)
    text = message.text.strip()

    if step == "waiting_for_api_id":
        if not text.isdigit():
            await message.reply_text("API_ID অবশ্যই সংখ্যা হতে হবে। আবার দিন।")
            return
        user_temp_data[user_id]["api_id"] = int(text)
        user_steps[user_id] = "waiting_for_api_hash"
        await message.reply_text("এখন আপনার **API_HASH** দিন:")

    elif step == "waiting_for_api_hash":
        user_temp_data[user_id]["api_hash"] = text
        user_steps[user_id] = "waiting_for_phone"
        await message.reply_text("এখন আপনার **ফোন নাম্বার** দিন (+880... ফরম্যাটে):")

    elif step == "waiting_for_phone":
        phone = text
        if not phone.startswith("+") or len(phone) < 10:
            await message.reply_text("ভুল ফোন নাম্বার। +880... ফরম্যাটে দিন।")
            return
        user_temp_data[user_id]["phone_number"] = phone
        user_steps[user_id] = "waiting_for_code"
        await send_code(user_id)

    elif step == "waiting_for_code":
        otp = text
        user_temp_data[user_id]["otp_code"] = otp
        await sign_in_user(user_id)

    elif step == "waiting_for_2fa_password":
        password = text
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
    api_id = data["api_id"]
    api_hash = data["api_hash"]

    # create temporary client once
    if session_type == "pyrogram":
        temp_client = Client(":memory:", api_id=api_id, api_hash=api_hash)
    else:
        temp_client = TelegramClient(StringSession(), api_id, api_hash)

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

        await bot.send_message(
            user_id,
            f"✅ আপনার {session_type.capitalize()} session তৈরি হলো।\n\n"
            f"```python\n{session_str}\n```\n\n"
            "**দয়া করে এটি নিরাপদ স্থানে সংরক্ষণ করুন। কারো সাথে share করবেন না।**",
            parse_mode="markdown"
        )
        cleanup_user(user_id)
    except (SessionPasswordNeeded, SessionPasswordNeededError):
        user_steps[user_id] = "waiting_for_2fa_password"
        await bot.send_message(user_id, "2FA password দিন।")
        return
    except (PhoneCodeInvalid, PhoneCodeInvalidError):
        await bot.send_message(user_id, "ভুল OTP! /generate দিয়ে আবার চেষ্টা করুন।")
        cleanup_user(user_id)
    except Exception as e:
        await bot.send_message(user_id, f"Sign in error: {e}")
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

        await bot.send_message(
            user_id,
            f"✅ আপনার {session_type.capitalize()} session তৈরি হলো।\n\n"
            f"```python\n{session_str}\n```\n\n"
            "**দয়া করে এটি নিরাপদ স্থানে সংরক্ষণ করুন। কারো সাথে share করবেন না।**",
            parse_mode="markdown"
        )
    except (PasswordHashInvalid, PasswordHashInvalidError):
        await bot.send_message(user_id, "ভুল পাসওয়ার্ড! /generate দিয়ে আবার চেষ্টা করুন।")
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
    Thread(target=run_flask).start()
    run_bot()
