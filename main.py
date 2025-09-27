import os
import asyncio
import requests # এটি Webhook সেট করার জন্য প্রয়োজন
from threading import Thread
from flask import Flask, request
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError, PasswordHashInvalidError, FloodWaitError
)

# NOTE: নিশ্চিত করুন 'requests' library ইনস্টল করা আছে।

BOT_TOKEN = os.environ.get("BOT_TOKEN")
# Pyrogram client for handling bot logic
bot = Client("webhook_bot", bot_token=BOT_TOKEN)
app = Flask(__name__)

# ----------------------
# User session tracking
# ----------------------
user_steps = {}
user_temp_data = {}

def cleanup_user(user_id):
    if user_id in user_steps: del user_steps[user_id]
    if user_id in user_temp_data: del user_temp_data[user_id]

async def timeout_cleanup(user_id, delay=300):
    await asyncio.sleep(delay)
    if user_id in user_steps:
        await bot.send_message(user_id, "OTP expired। `/generate` দিয়ে আবার শুরু করুন।")
        cleanup_user(user_id)

# ----------------------
# Bot Handlers 
# ----------------------
@bot.on_message(filters.command("start") & filters.private)
async def start_handler(_, message):
    await message.reply_text(
        "নমস্কার! আমি Telegram string session generator bot।\n"
        "আপনার Pyrogram বা Telethon session তৈরি করতে `/generate` দিন।"
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
    await query.message.edit(f"{lib_choice.capitalize()} বেছে নেওয়া হলো। এখন আপনার API_ID দিন:")

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
        await message.reply_text("এখন আপনার API_HASH দিন:")

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

# ----------------------
# Pyrogram OTP
# ----------------------
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeInvalid

async def send_pyrogram_code(user_id):
    data = user_temp_data[user_id]
    client = Client(":memory:", api_id=data["api_id"], api_hash=data["api_hash"])
    await client.connect()
    data["temp_client"] = client
    try:
        sent = await client.send_code(data["phone_number"])
        data["sent_code_info"] = sent
        await bot.send_message(user_id, "OTP পাঠানো হলো। 5 মিনিটের মধ্যে submit করুন।")
        asyncio.create_task(timeout_cleanup(user_id,300))
    except Exception as e:
        await bot.send_message(user_id, f"OTP পাঠাতে সমস্যা: {e}")
        cleanup_user(user_id)

async def sign_in_pyrogram(user_id):
    data = user_temp_data[user_id]
    client = data["temp_client"]
    try:
        await client.sign_in(data["phone_number"], data["sent_code_info"].phone_code_hash, data["otp_code"])
        session_str = await client.export_session_string()
        await client.disconnect() # Disconnect the temporary client
        await bot.send_message(user_id,f"✅ Session তৈরি হলো:\n\n```python\n{session_str}\n```", parse_mode="markdown")
        cleanup_user(user_id)
    except SessionPasswordNeeded:
        user_steps[user_id] = "waiting_for_2fa_password"
        await bot.send_message(user_id,"2FA পাসওয়ার্ড দিন।")
    except PhoneCodeInvalid:
        await client.disconnect() # Disconnect on failure
        await bot.send_message(user_id,"ভুল OTP। আবার `/generate` দিন।")
        cleanup_user(user_id)
    except Exception as e:
        await client.disconnect() # Disconnect on failure
        await bot.send_message(user_id,f"Sign in error: {e}")
        cleanup_user(user_id)

async def check_pyrogram_password(user_id):
    data = user_temp_data[user_id]
    client = data["temp_client"]
    try:
        await client.check_password(data["password"])
        session_str = await client.export_session_string()
        await bot.send_message(user_id,f"✅ Session তৈরি হলো:\n\n```python\n{session_str}\n```", parse_mode="markdown")
    except Exception as e:
        await bot.send_message(user_id,f"Password error: {e}")
    finally:
        await client.disconnect() # Disconnect the temporary client
        cleanup_user(user_id)

# ----------------------
# Telethon OTP
# ----------------------
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
        await bot.send_message(user_id,f"OTP পাঠাতে সমস্যা: {e}")
        cleanup_user(user_id)

async def sign_in_telethon(user_id):
    data = user_temp_data[user_id]
    client = data["temp_client"]
    try:
        await client.sign_in(data["phone_number"], data["otp_code"])
        session_str = client.session.save()
        await client.disconnect() # Disconnect the temporary client
        await bot.send_message(user_id,f"✅ Session তৈরি হলো:\n\n```python\n{session_str}\n```", parse_mode="markdown")
        cleanup_user(user_id)
    except SessionPasswordNeededError:
        user_steps[user_id] = "waiting_for_2fa_password"
        await bot.send_message(user_id,"2FA পাসওয়ার্ড দিন।")
    except PhoneCodeInvalidError:
        await client.disconnect() # Disconnect on failure
        await bot.send_message(user_id,"ভুল OTP। আবার `/generate` দিন।")
        cleanup_user(user_id)
    except Exception as e:
        await client.disconnect() # Disconnect on failure
        await bot.send_message(user_id,f"Sign in error: {e}")
        cleanup_user(user_id)

async def check_telethon_password(user_id):
    data = user_temp_data[user_id]
    client = data["temp_client"]
    try:
        await client.sign_in(password=data["password"])
        session_str = client.session.save()
        await bot.send_message(user_id,f"✅ Session তৈরি হলো:\n\n```python\n{session_str}\n```", parse_mode="markdown")
    except PasswordHashInvalidError:
        await bot.send_message(user_id,"ভুল পাসওয়ার্ড। `/generate` দিয়ে আবার শুরু করুন।")
    except Exception as e:
        await bot.send_message(user_id,f"Password error: {e}")
    finally:
        await client.disconnect() # Disconnect the temporary client
        cleanup_user(user_id)

# ----------------------
# Flask Webhook route
# ----------------------
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = request.get_json()
    if update:
        # We need to run the Pyrogram processing in the async loop
        asyncio.run(bot.process_new_updates([update]))
    return "ok"

# ----------------------
# Set webhook (Synchronous function using requests)
# ----------------------
def set_webhook_sync(url):
    """Pyrogram ক্লায়েন্টের জন্য সরাসরি Telegram API কল করে Webhook সেট করে।"""
    
    webhook_url = f"{url}/{BOT_TOKEN}"
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    params = {"url": webhook_url}
    
    try:
        response = requests.post(api_url, params=params, timeout=10)
        response.raise_for_status() 
        result = response.json()
        if result.get("ok"):
            print(f"Webhook set successfully to: {webhook_url}")
        else:
            print(f"Failed to set webhook. API response: {result}")
    except requests.exceptions.RequestException as e:
        print(f"Error setting webhook: {e}")

# ----------------------
# Run Flask + Bot
# ----------------------
if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()

    # 1. Set Webhook synchronously before starting the server
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if url:
        set_webhook_sync(url)
    else:
        print("WARNING: RENDER_EXTERNAL_URL environment variable is not set. Webhook will not be configured.")

    # 2. Start the Flask server thread
    try:
        port = int(os.environ.get("PORT", 5000))
    except ValueError:
        port = 5000
    
    Thread(target=lambda: app.run(host="0.0.0.0", port=port)).start()
    
    # 3. Start Pyrogram client loop for background tasks (like cleanup)
    bot.run()
