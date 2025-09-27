import os
import asyncio
from flask import Flask, request, jsonify
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

# --- ENVIRONMENT VARIABLES ---
API_ID = int(os.environ.get("API_ID", "YOUR_API_ID"))
API_HASH = os.environ.get("API_HASH", "YOUR_API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")
PORT = int(os.environ.get("PORT", 5000))  # Render স্বয়ংক্রিয়ভাবে PORT সেট করে দেয়

# --- Flask app ---
app = Flask(__name__)

# Pyrogram bot client
bot = Client("session_generator_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ব্যবহারকারীর ডেটা সংরক্ষণের জন্য ডিকশনারি
user_steps = {}
user_temp_data = {}

# --- Flask Routes --- #
@app.route("/")
def index():
    return "Telegram Session Generator Bot is running on Render!"

@app.route("/start_bot", methods=["POST"])
def start_bot():
    """Render এ HTTP POST request এ বট চালু হবে"""
    loop = asyncio.get_event_loop()
    loop.create_task(bot.start())
    return jsonify({"status": "Bot started"}), 200

@app.route("/stop_bot", methods=["POST"])
async def stop_bot():
    """বট বন্ধ করার জন্য"""
    await bot.stop()
    return jsonify({"status": "Bot stopped"}), 200

# --- Pyrogram Handlers --- #
@bot.on_message(filters.command("start") & filters.private)
async def start_handler(_, message):
    await message.reply_text(
        "হ্যালো! আমি একটি টেলিগ্রাম স্ট্রিং সেশন জেনারেটর বট।\n\n"
        "আপনার Pyrogram বা Telethon স্ট্রিং সেশন তৈরি করতে `/generate` কমান্ডটি ব্যবহার করুন।\n\n"
        "**সতর্কতা:** আপনার কোনো তথ্যই আমি সংরক্ষণ করি না।"
    )

@bot.on_message(filters.command("generate") & filters.private)
async def generate_command_handler(_, message):
    user_id = message.from_user.id
    
    if user_id in user_steps:
        await message.reply_text("আপনার পূর্ববর্তী সেশন তৈরির প্রক্রিয়া বাতিল করা হয়েছে।")
        del user_steps[user_id]
        if user_id in user_temp_data:
            del user_temp_data[user_id]
            
    await message.reply_text(
        "আপনি কোন লাইব্রেরির জন্য স্ট্রিং সেশন তৈরি করতে চান?\n\n"
        "প্রক্রিয়া বাতিল করতে যে কোনো সময় `/cancel` লিখুন।",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Pyrogram V2", callback_data="gen_pyrogram")],
            [InlineKeyboardButton("Telethon", callback_data="gen_telethon")]
        ])
    )
    user_steps[user_id] = "choosing_session_type"

@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_handler(_, message):
    user_id = message.from_user.id
    if user_id in user_steps:
        del user_steps[user_id]
        if user_id in user_temp_data:
            del user_temp_data[user_id]
        await message.reply_text("সেশন তৈরির প্রক্রিয়া বাতিল করা হয়েছে।")
    else:
        await message.reply_text("কোনো সক্রিয় প্রক্রিয়া নেই যা বাতিল করা যায়।")

@bot.on_callback_query(filters.regex("^gen_"))
async def callback_query_handler(client: Client, callback_query):
    session_type = callback_query.data.split("_")[1]
    user_id = callback_query.from_user.id
    chat_id = callback_query.message.chat.id
    
    if user_id not in user_steps or user_steps[user_id] != "choosing_session_type":
        await callback_query.answer("দয়া করে /generate কমান্ড ব্যবহার করে আবার শুরু করুন।", show_alert=True)
        return
        
    await callback_query.message.delete()

    user_temp_data[user_id] = {"session_type": session_type}
    user_steps[user_id] = "waiting_for_phone_number"
    
    await client.send_message(
        chat_id, 
        f"আপনি **{session_type.capitalize()}** সেশন তৈরি করতে বেছে নিয়েছেন।\n\n"
        "দয়া করে আপনার টেলিগ্রাম অ্যাকাউন্টের ফোন নম্বরটি আন্তর্জাতিক ফরম্যাটে পাঠান (যেমন: `+8801712345678`)।"
    )

@bot.on_message(filters.text & filters.private & ~filters.command(["start", "generate", "cancel"]))
async def handle_user_input(client: Client, message):
    user_id = message.from_user.id
    current_step = user_steps.get(user_id)
    
    if current_step == "waiting_for_phone_number":
        user_temp_data[user_id]["phone_number"] = message.text
        user_steps[user_id] = "waiting_for_code"
        await send_code_and_ask_for_otp(client, message.chat.id, user_id)
    elif current_step == "waiting_for_code":
        user_temp_data[user_id]["otp_code"] = message.text
        user_steps[user_id] = "waiting_for_2fa_password"
        await sign_in_and_generate_session(client, message.chat.id, user_id)
    elif current_step == "waiting_for_password":
        user_temp_data[user_id]["password"] = message.text
        del user_steps[user_id]
        await check_password_and_generate_session(client, message.chat.id, user_id)
    else:
        if user_id in user_steps:
            await message.reply_text("দয়া করে আপনার ইনপুটটি সঠিক ধাপে দিন অথবা `/cancel` লিখে প্রক্রিয়া বাতিল করুন।")
        else:
            await message.reply_text("আপনার সেশন তৈরি শুরু করতে `/generate` কমান্ড ব্যবহার করুন।")

# --- Helper Functions --- #
async def send_code_and_ask_for_otp(client, chat_id, user_id):
    phone_number = user_temp_data[user_id]["phone_number"]
    session_type = user_temp_data[user_id]["session_type"]
    
    if session_type == "pyrogram":
        temp_client = Client(":memory:", api_id=API_ID, api_hash=API_HASH)
        user_temp_data[user_id]["temp_client"] = temp_client
        try:
            await temp_client.connect()
            sent_code = await temp_client.send_code(phone_number)
            user_temp_data[user_id]["sent_code_info"] = sent_code
            await client.send_message(chat_id, "আপনার টেলিগ্রাম অ্যাপে একটি ভেরিফিকেশন কোড পাঠানো হয়েছে। দয়া করে সেই কোডটি এখানে দিন (যেমন: `12345`)।")
        except PhoneNumberInvalid:
            await client.send_message(chat_id, "ভুল ফোন নম্বর! `/generate` কমান্ড ব্যবহার করুন।")
            del user_steps[user_id]
            del user_temp_data[user_id]
        except FloodWait as e:
            await client.send_message(chat_id, f"অনেক চেষ্টা হয়েছে। {e.value} সেকেন্ড পর আবার চেষ্টা করুন।")
            del user_steps[user_id]
            del user_temp_data[user_id]
        except Exception as e:
            await client.send_message(chat_id, f"কোড পাঠাতে ত্রুটি: {e}")
            del user_steps[user_id]
            del user_temp_data[user_id]

    elif session_type == "telethon":
        temp_client = TelegramClient(StringSession(), API_ID, API_HASH)
        user_temp_data[user_id]["temp_client"] = temp_client
        try:
            await temp_client.connect()
            await temp_client.send_code_request(phone_number)
            await client.send_message(chat_id, "আপনার টেলিগ্রাম অ্যাপে একটি ভেরিফিকেশন কোড পাঠানো হয়েছে। দয়া করে সেই কোডটি এখানে দিন।")
        except PhoneNumberInvalidError:
            await client.send_message(chat_id, "ভুল ফোন নম্বর! `/generate` কমান্ড ব্যবহার করুন।")
            del user_steps[user_id]
            del user_temp_data[user_id]
        except FloodWaitError as e:
            await client.send_message(chat_id, f"অনেক চেষ্টা হয়েছে। {e.value} সেকেন্ড পর আবার চেষ্টা করুন।")
            del user_steps[user_id]
            del user_temp_data[user_id]
        except Exception as e:
            await client.send_message(chat_id, f"কোড পাঠাতে ত্রুটি: {e}")
            del user_steps[user_id]
            del user_temp_data[user_id]

async def sign_in_and_generate_session(client, chat_id, user_id):
    phone_number = user_temp_data[user_id]["phone_number"]
    otp_code = user_temp_data[user_id]["otp_code"]
    session_type = user_temp_data[user_id]["session_type"]
    temp_client = user_temp_data[user_id]["temp_client"]
    
    if session_type == "pyrogram":
        try:
            await temp_client.sign_in(phone_number, user_temp_data[user_id]["sent_code_info"].phone_code_hash, otp_code)
            session_string = await temp_client.export_session_string()
            await client.send_message(chat_id, f"আপনার Pyrogram স্ট্রিং সেশন:\n```python\n{session_string}\n```")
        except SessionPasswordNeeded:
            user_steps[user_id] = "waiting_for_password"
            await client.send_message(chat_id, "2FA পাসওয়ার্ড দিন।")
            return
        except PhoneCodeInvalid:
            await client.send_message(chat_id, "ভুল কোড! `/generate`")
        except FloodWait as e:
            await client.send_message(chat_id, f"{e.value} সেকেন্ড অপেক্ষা করুন।")
        except Exception as e:
            await client.send_message(chat_id, f"ত্রুটি: {e}")
        finally:
            del user_steps[user_id]
            del user_temp_data[user_id]
            await temp_client.disconnect()

    elif session_type == "telethon":
        try:
            await temp_client.sign_in(phone_number, otp_code)
            session_string = temp_client.session.save()
            await client.send_message(chat_id, f"আপনার Telethon স্ট্রিং সেশন:\n```python\n{session_string}\n```")
        except SessionPasswordNeededError:
            user_steps[user_id] = "waiting_for_password"
            await client.send_message(chat_id, "2FA পাসওয়ার্ড দিন।")
            return
        except PhoneCodeInvalidError:
            await client.send_message(chat_id, "ভুল কোড! `/generate`")
        except FloodWaitError as e:
            await client.send_message(chat_id, f"{e.value} সেকেন্ড অপেক্ষা করুন।")
        except Exception as e:
            await client.send_message(chat_id, f"ত্রুটি: {e}")
        finally:
            del user_steps[user_id]
            del user_temp_data[user_id]
            await temp_client.disconnect()

async def check_password_and_generate_session(client, chat_id, user_id):
    password = user_temp_data[user_id]["password"]
    session_type = user_temp_data[user_id]["session_type"]
    temp_client = user_temp_data[user_id]["temp_client"]
    
    if session_type == "pyrogram":
        try:
            await temp_client.check_password(password)
            session_string = await temp_client.export_session_string()
            await client.send_message(chat_id, f"আপনার Pyrogram স্ট্রিং সেশন:\n```python\n{session_string}\n```")
        except PasswordHashInvalid:
            await client.send_message(chat_id, "ভুল পাসওয়ার্ড! `/generate`")
        except FloodWait as e:
            await client.send_message(chat_id, f"{e.value} সেকেন্ড অপেক্ষা করুন।")
        except Exception as e:
            await client.send_message(chat_id, f"ত্রুটি: {e}")
        finally:
            del user_steps[user_id]
            del user_temp_data[user_id]
            await temp_client.disconnect()

    elif session_type == "telethon":
        try:
            await temp_client.sign_in(password=password)
            session_string = temp_client.session.save()
            await client.send_message(chat_id, f"আপনার Telethon স্ট্রিং সেশন:\n```python\n{session_string}\n```")
        except PasswordHashInvalidError:
            await client.send_message(chat_id, "ভুল পাসওয়ার্ড! `/generate`")
        except FloodWaitError as e:
            await client.send_message(chat_id, f"{e.value} সেকেন্ড অপেক্ষা করুন।")
        except Exception as e:
            await client.send_message(chat_id, f"ত্রুটি: {e}")
        finally:
            del user_steps[user_id]
            del user_temp_data[user_id]
            await temp_client.disconnect()

# --- Run Flask app --- #
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
