# main.py
import os
import asyncio
from flask import Flask
from threading import Thread

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import (
    SessionPasswordNeeded, PhoneCodeInvalid, PasswordHashInvalid, PhoneNumberInvalid,
    FloodWait
)
from telethon.sessions import StringSession
from telethon.sync import TelegramClient as TeleClient
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError, PasswordHashInvalidError, PhoneNumberInvalidError,
    FloodWaitError
)

# ----------------------------
# Bot credentials (ENV variables)
# ----------------------------
BOT_API_ID = int(os.environ.get("API_ID", 0))
BOT_API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

if not BOT_TOKEN:
    raise Exception("BOT_TOKEN environment variable set করুন।")

# ----------------------------
# Pyrogram bot client
# ----------------------------
bot = Client(
    "session_generator_bot",
    api_id=BOT_API_ID,
    api_hash=BOT_API_HASH,
    bot_token=BOT_TOKEN
)

# ----------------------------
# Temporary in-memory data
# ----------------------------
user_steps = {}      # {user_id: step_name}
user_temp_data = {}  # {user_id: {api_id, api_hash, phone_number, sent_code_info, session_type, temp_client}}

# ----------------------------
# Helper to clean temporary data
# ----------------------------
def cleanup_user(user_id):
    try:
        if user_id in user_steps:
            del user_steps[user_id]
        if user_id in user_temp_data:
            temp_client = user_temp_data[user_id].get("temp_client")
            if temp_client:
                try:
                    loop = asyncio.get_event_loop()
                    loop.create_task(temp_client.disconnect())
                except:
                    pass
            del user_temp_data[user_id]
    except:
        pass

# ----------------------------
# /start handler
# ----------------------------
@bot.on_message(filters.command("start") & filters.private)
async def start_handler(_, message: Message):
    await message.reply_text(
        "নমস্কার! আমি একটি টেলিগ্রাম স্ট্রিং সেশন জেনারেটর বট।\n\n"
        "আপনার নিজের API_ID, API_HASH এবং ফোন নম্বর দিয়ে session তৈরি করতে `/generate` কমান্ড ব্যবহার করুন।\n\n"
        "**সতর্কতা:** আপনার তথ্য আমি সংরক্ষণ করি না।"
    )

# ----------------------------
# /generate handler
# ----------------------------
@bot.on_message(filters.command("generate") & filters.private)
async def generate_command_handler(_, message: Message):
    user_id = message.from_user.id
    cleanup_user(user_id)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Pyrogram", callback_data="gen_pyrogram"),
         InlineKeyboardButton("Telethon", callback_data="gen_telethon")],
        [InlineKeyboardButton("Cancel", callback_data="gen_cancel")]
    ])
    user_steps[user_id] = "choosing_session_type"
    await message.reply_text(
        "আপনি কোন লাইব্রেরির জন্য session তৈরি করতে চান?\n\n"
        "প্রক্রিয়া বাতিল করতে পরে Cancel চাপুন।",
        reply_markup=keyboard
    )

# ----------------------------
# Callback query handler
# ----------------------------
@bot.on_callback_query(filters.regex(r"^gen_(pyrogram|telethon|cancel)$"))
async def callback_handler(client, callback_query):
    user_id = callback_query.from_user.id
    data = callback_query.data

    if data == "gen_cancel":
        cleanup_user(user_id)
        await callback_query.message.delete()
        await callback_query.answer("প্রক্রিয়া বাতিল করা হয়েছে।", show_alert=True)
        return

    session_type = data.split("_")[1]
    user_temp_data[user_id] = {"session_type": session_type}
    user_steps[user_id] = "waiting_for_api_id"
    await callback_query.message.delete()
    await client.send_message(user_id, "প্রথমে আপনার **API_ID** দিন (শুধু সংখ্যা)।")

# ----------------------------
# Handle user text input
# ----------------------------
@bot.on_message(filters.text & filters.private & ~filters.command(["start", "generate", "cancel"]))
async def handle_user_input(client, message: Message):
    user_id = message.from_user.id
    step = user_steps.get(user_id)
    text = message.text.strip()

    if step == "waiting_for_api_id":
        try:
            api_id = int(text)
        except:
            await message.reply_text("API_ID অবশ্যই সংখ্যা হতে হবে। আবার পাঠান।")
            return
        user_temp_data[user_id]["api_id"] = api_id
        user_steps[user_id] = "waiting_for_api_hash"
        await message.reply_text("এখন আপনার **API_HASH** দিন।")
        return

    if step == "waiting_for_api_hash":
        api_hash = text
        user_temp_data[user_id]["api_hash"] = api_hash
        user_steps[user_id] = "waiting_for_phone_number"
        await message.reply_text("এখন আপনার **PHONE NUMBER** দিন (উদাহরণ: +8801712345678)।")
        return

    if step == "waiting_for_phone_number":
        phone = text
        user_temp_data[user_id]["phone_number"] = phone
        user_steps[user_id] = "waiting_for_code"
        await send_code_and_ask_for_otp(client, user_id)
        return

    if step == "waiting_for_code":
        user_temp_data[user_id]["otp_code"] = text
        user_steps[user_id] = "waiting_for_password"
        await sign_in_and_generate_session(client, user_id)
        return

    if step == "waiting_for_password":
        user_temp_data[user_id]["password"] = text
        await check_password_and_generate_session(client, user_id)
        return

    await message.reply_text("অপ্রত্যাশিত ইনপুট। `/generate` দিয়ে আবার শুরু করুন অথবা `/cancel` করুন।")

# ----------------------------
# Send verification code
# ----------------------------
async def send_code_and_ask_for_otp(client, user_id):
    data = user_temp_data[user_id]
    phone = data["phone_number"]
    api_id = data["api_id"]
    api_hash = data["api_hash"]
    session_type = data["session_type"]

    if session_type == "pyrogram":
        temp_client = Client(":memory:", api_id=api_id, api_hash=api_hash)
        data["temp_client"] = temp_client
        try:
            await temp_client.connect()
            sent = await temp_client.send_code(phone)
            data["sent_code_info"] = sent
            await client.send_message(user_id, "ভেরিফিকেশন কোড পাঠানো হয়েছে। কোড দিন।")
        except Exception as e:
            await client.send_message(user_id, f"কোড পাঠাতে ত্রুটি: {e}")
            cleanup_user(user_id)

    elif session_type == "telethon":
        temp_client = TeleClient(StringSession(), api_id, api_hash)
        data["temp_client"] = temp_client
        try:
            await temp_client.connect()
            await temp_client.send_code_request(phone)
            await client.send_message(user_id, "ভেরিফিকেশন কোড পাঠানো হয়েছে। কোড দিন।")
        except Exception as e:
            await client.send_message(user_id, f"কোড পাঠাতে ত্রুটি: {e}")
            cleanup_user(user_id)

# ----------------------------
# Sign in and generate session
# ----------------------------
async def sign_in_and_generate_session(client, user_id):
    data = user_temp_data[user_id]
    phone = data["phone_number"]
    otp_code = data["otp_code"]
    session_type = data["session_type"]
    temp_client = data["temp_client"]

    if session_type == "pyrogram":
        try:
            phone_code_hash = getattr(data["sent_code_info"], "phone_code_hash", None)
            await temp_client.sign_in(phone, phone_code_hash, otp_code)
            session_string = await temp_client.export_session_string()
            await client.send_message(user_id, f"Pyrogram session:\n```python\n{session_string}\n```")
        except SessionPasswordNeeded:
            user_steps[user_id] = "waiting_for_password"
            await client.send_message(user_id, "2FA পাসওয়ার্ড দিন।")
            return
        except Exception as e:
            await client.send_message(user_id, f"সাইন ইন ত্রুটি: {e}")
        finally:
            await temp_client.disconnect()
            cleanup_user(user_id)

    elif session_type == "telethon":
        try:
            await temp_client.sign_in(phone, otp_code)
            session_string = temp_client.session.save()
            await client.send_message(user_id, f"Telethon session:\n```python\n{session_string}\n```")
        except SessionPasswordNeededError:
            user_steps[user_id] = "waiting_for_password"
            await client.send_message(user_id, "2FA পাসওয়ার্ড দিন।")
            return
        except Exception as e:
            await client.send_message(user_id, f"সাইন ইন ত্রুটি: {e}")
        finally:
            await temp_client.disconnect()
            cleanup_user(user_id)

# ----------------------------
# Check 2FA password and generate session
# ----------------------------
async def check_password_and_generate_session(client, user_id):
    data = user_temp_data[user_id]
    session_type = data["session_type"]
    temp_client = data["temp_client"]
    password = data["password"]

    if session_type == "pyrogram":
        try:
            await temp_client.check_password(password)
            session_string = await temp_client.export_session_string()
            await client.send_message(
                user_id,
                f"Pyrogram session (2FA):\n```python\n{session_string}\n```"
            )
        except PasswordHashInvalid:
            await client.send_message(user_id, "ভুল পাসওয়ার্ড! প্রক্রিয়া বাতিল।")
        except Exception as e:
            await client.send_message(user_id, f"পাসওয়ার্ড যাচাই ত্রুটি: {e}")
        finally:
            await temp_client.disconnect()
            cleanup_user(user_id)

    elif session_type == "telethon":
        try:
            await temp_client.sign_in(password=password)
            session_string = temp_client.session.save()
            await client.send_message(
                user_id,
                f"Telethon session (2FA):\n```python\n{session_string}\n```"
            )
        except PasswordHashInvalidError:
            await client.send_message(user_id, "ভুল পাসওয়ার্ড! প্রক্রিয়া বাতিল।")
        except Exception as e:
            await client.send_message(user_id, f"পাসওয়ার্ড যাচাই ত্রুটি: {e}")
        finally:
            await temp_client.disconnect()
            cleanup_user(user_id)

# ----------------------------
# /cancel handler
# ----------------------------
@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_handler(_, message: Message):
    user_id = message.from_user.id
    cleanup_user(user_id)
    await message.reply_text("প্রক্রিয়া বাতিল করা হয়েছে।")

# ----------------------------
# Flask web server for Render port
# ----------------------------
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

# ----------------------------
# Bot run in background thread
# ----------------------------
def run_bot():
    bot.run()

# ----------------------------
# Main entry
# ----------------------------
if __name__ == "__main__":
    # Flask server thread
    flask_thread = Thread(target=run_flask)
    flask_thread.start()

    # Bot run
    run_bot()
