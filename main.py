import os
import asyncio
import requests
import json
from threading import Thread
from flask import Flask, request, jsonify
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeInvalid
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
SessionPasswordNeededError, PhoneCodeInvalidError, PasswordHashInvalidError, FloodWaitError
)
Environment Variables
BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = os.environ.get("API_ID")  # REQUIRED FOR PYROGRAM CONNECT
API_HASH = os.environ.get("API_HASH") # REQUIRED FOR PYROGRAM CONNECT
Client Initialization
Note: Pyrogram now requires API_ID and API_HASH even for bot tokens
try:
# Changed int(API_ID) to API_ID as Pyrogram handles type conversion
bot = Client("webhook_bot", bot_token=BOT_TOKEN, api_id=API_ID, api_hash=API_HASH)
except Exception as e:
# Print error but continue to allow non-pyrogram specific testing
print(f"Error initializing Pyrogram Client: {e}")
# Fallback initialization (might fail later if API_ID/HASH are truly mandatory)
bot = Client("webhook_bot", bot_token=BOT_TOKEN)
app = Flask(name)
----------------------
User session tracking
----------------------
user_steps = {}
user_temp_data = {}
def cleanup_user(user_id):
if user_id in user_steps: del user_steps[user_id]
if user_id in user_temp_data: del user_temp_data[user_id]
async def timeout_cleanup(user_id, delay=300):
await asyncio.sleep(delay)
if user_id in user_steps:
await bot.send_message(user_id, "OTP expired। /generate দিয়ে আবার শুরু করুন।")
cleanup_user(user_id)
----------------------
Bot Handlers
----------------------
@bot.on_message(filters.command("start") & filters.private)
async def start_handler(_, message):
await message.reply_text(
"নমস্কার! আমি Telegram string session generator bot।\n"
"আপনার Pyrogram বা Telethon session তৈরি করতে /generate দিন।"
)
@bot.on_message(filters.command("generate") & filters.private)
async def generate_handler(_, message):
user_id = message.from_user.id
cleanup_user(user_id)
user_steps[user_id] = "waiting_for_lib_choice"
await message.reply_text(
"কোন লাইব্রেরি ব্যবহার করতে চান?",
reply_markup=InlineKeyboardMarkup([
[InlineKeyboardButton("Pyrogram", callback_data="lib_pyrogram")],
[InlineKeyboardButton("Telethon", callback_data="lib_telethon")]
])
)
@bot.on_callback_query(filters.regex("^lib_"))
async def callback_handler(client, query):
user_id = query.from_user.id
lib_choice = query.data.split("_")[1]
user_temp_data[user_id] = {"library": lib_choice}
user_steps[user_id] = "waiting_for_api_id"
# Escaping underscores in API_ID/HASH to prevent MarkdownV2 parse error
await query.message.edit_text(f"{lib_choice.capitalize()} বেছে নেওয়া হলো। এখন আপনার API_ID দিন:", parse_mode="markdown")
@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_handler(_, message):
user_id = message.from_user.id
cleanup_user(user_id)
await message.reply_text("প্রক্রিয়া বাতিল করা হলো।")
@bot.on_message(filters.text & filters.private & ~filters.command(["start","generate","cancel"]))
async def handle_input(_, message):
user_id = message.from_user.id
step = user_steps.get(user_id)
text = message.text.strip()
if step == "waiting_for_api_id":
    if not text.isdigit():
        await message.reply_text("API_ID অবশ্যই সংখ্যা হতে হবে। আবার দিন।")
        return
    user_temp_data[user_id]["api_id"] = int(text)
    user_steps[user_id] = "waiting_for_api_hash"
    await message.reply_text("এখন আপনার API\_HASH দিন:", parse_mode="markdown") # Escaped underscore

elif step == "waiting_for_api_hash":
    user_temp_data[user_id]["api_hash"] = text
    user_steps[user_id] = "waiting_for_phone"
    await message.reply_text("এখন আপনার ফোন নাম্বার দিন (+880...) ফরম্যাটে:")

elif step == "waiting_for_phone":
    phone = text
    if not phone.startswith("+") or len(phone)<10:
        await message.reply_text("ভুল ফোন নাম্বার। +880... ফরম্যাটে দিন।")
        return
    user_temp_data[user_id]["phone_number"] = phone
    lib = user_temp_data[user_id]["library"]
    if lib == "pyrogram":
        user_steps[user_id] = "waiting_for_code"
        await send_pyrogram_code(user_id)
    else:
        user_steps[user_id] = "waiting_for_code"
        await send_telethon_code(user_id)

elif step == "waiting_for_code":
    user_temp_data[user_id]["otp_code"] = text
    lib = user_temp_data[user_id]["library"]
    if lib == "pyrogram":
        await sign_in_pyrogram(user_id)
    else:
        await sign_in_telethon(user_id)

elif step == "waiting_for_2fa_password":
    user_temp_data[user_id]["password"] = text
    lib = user_temp_data[user_id]["library"]
    if lib == "pyrogram":
        await check_pyrogram_password(user_id)
    else:
        await check_telethon_password(user_id)
else:
    await message.reply_text("প্রক্রিয়া শুরু করতে `/generate` দিন।")

----------------------
Pyrogram OTP
----------------------
async def send_pyrogram_code(user_id):
data = user_temp_data[user_id]
# Use memory client for temporary session
client = Client(":memory:", api_id=data["api_id"], api_hash=data["api_hash"])
await client.connect()
data["temp_client"] = client
try:
sent = await client.send_code(data["phone_number"])
data["sent_code_info"] = sent
await bot.send_message(user_id, "OTP পাঠানো হলো। 5 মিনিটের মধ্যে submit করুন।")
asyncio.create_task(timeout_cleanup(user_id,300))
except Exception as e:
await bot.send_message(user_id, f"❌ OTP পাঠাতে সমস্যা: {e}")
cleanup_user(user_id)
async def sign_in_pyrogram(user_id):
data = user_temp_data[user_id]
client = data["temp_client"]
try:
await client.sign_in(data["phone_number"], data["sent_code_info"].phone_code_hash, data["otp_code"])
session_str = await client.export_session_string()
# FIX: Removed parse_mode. The code block format is recognized automatically.
await bot.send_message(user_id,f"✅ Session তৈরি হলো:\n\npython\n{session_str}\n")
cleanup_user(user_id)
except SessionPasswordNeeded:
user_steps[user_id] = "waiting_for_2fa_password"
await bot.send_message(user_id,"2FA পাসওয়ার্ড দিন।")
except PhoneCodeInvalid:
await bot.send_message(user_id,"❌ ভুল OTP। আবার /generate দিন।")
cleanup_user(user_id)
except Exception as e:
await bot.send_message(user_id,f"❌ Sign in error: {e}")
cleanup_user(user_id)
async def check_pyrogram_password(user_id):
data = user_temp_data[user_id]
client = data["temp_client"]
try:
await client.check_password(data["password"])
session_str = await client.export_session_string()
# FIX: Removed parse_mode
await bot.send_message(user_id,f"✅ Session তৈরি হলো:\n\npython\n{session_str}\n")
except Exception as e:
await bot.send_message(user_id,f"❌ Password error: {e}")
finally:
cleanup_user(user_id)
----------------------
Telethon OTP
----------------------
async def send_telethon_code(user_id):
data = user_temp_data[user_id]
client = TelegramClient(StringSession(), api_id=data["api_id"], api_hash=data["api_hash"])
await client.connect()
data["temp_client"] = client
try:
await client.send_code_request(data["phone_number"])
await bot.send_message(user_id, "OTP পাঠানো হলো। 5 মিনিটের মধ্যে submit করুন।")
asyncio.create_task(timeout_cleanup(user_id,300))
except Exception as e:
await bot.send_message(user_id,f"❌ OTP পাঠাতে সমস্যা: {e}")
cleanup_user(user_id)
async def sign_in_telethon(user_id):
data = user_temp_data[user_id]
client = data["temp_client"]
try:
await client.sign_in(data["phone_number"], data["otp_code"])
session_str = client.session.save()
# FIX: Removed parse_mode
await bot.send_message(user_id,f"✅ Session তৈরি হলো:\n\npython\n{session_str}\n")
cleanup_user(user_id)
except SessionPasswordNeededError:
user_steps[user_id] = "waiting_for_2fa_password"
await bot.send_message(user_id,"2FA পাসওয়ার্ড দিন।")
except PhoneCodeInvalidError:
await bot.send_message(user_id,"❌ ভুল OTP। আবার /generate দিন।")
cleanup_user(user_id)
except Exception as e:
await bot.send_message(user_id,f"❌ Sign in error: {e}")
cleanup_user(user_id)
async def check_telethon_password(user_id):
data = user_temp_data[user_id]
client = data["temp_client"]
try:
await client.sign_in(password=data["password"])
session_str = client.session.save()
# FIX: Removed parse_mode
await bot.send_message(user_id,f"✅ Session তৈরি হলো:\n\npython\n{session_str}\n")
except PasswordHashInvalidError:
await bot.send_message(user_id,"❌ ভুল পাসওয়ার্ড। /generate দিয়ে আবার শুরু করুন।")
except Exception as e:
await bot.send_message(user_id,f"❌ Password error: {e}")
finally:
cleanup_user(user_id)
----------------------
Flask Webhook route (FIXED for robustness)
----------------------
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
async def webhook():
# Attempt to get JSON data
try:
update = request.get_json(silent=True)
except Exception as e:
print(f"Error parsing JSON: {e}")
update = None
if update:
    # Use asyncio.ensure_future or create_task to run pyrogram processing asynchronously
    asyncio.create_task(bot.process_new_updates([update]))
    
return jsonify({"status": "ok"})

----------------------
Set Webhook (FIXED: Uses requests library instead of bot.set_webhook)
----------------------
def set_webhook_sync():
"""Sets the webhook using a synchronous HTTP request."""
try:
url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("EXTERNAL_URL")
if not url:
print("❌ WARNING: EXTERNAL URL not found. Webhook will not be set.")
return
    webhook_url = f"{url}/{BOT_TOKEN}"
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    
    response = requests.post(api_url, json={'url': webhook_url})
    
    if response.status_code == 200 and response.json().get('ok'):
        print(f"✅ Webhook set successfully to: {webhook_url}")
    else:
        print(f"❌ Webhook failed to set. Response: {response.text}")
        
except Exception as e:
    print(f"❌ Error during webhook setup: {e}")

----------------------
Run Flask + Bot
----------------------
if name == "main":
import nest_asyncio
nest_asyncio.apply()
# 1. Set Webhook Synchronously
set_webhook_sync()

# 2. Start Flask Server in a separate Thread
Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))).start()

# 3. Run Pyrogram Bot (Starts the main Asyncio loop)
print("🚀 Starting Pyrogram Client...")
bot.run()

