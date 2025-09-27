import os
import asyncio
import requests # <--- Webhook সেট করার জন্য নতুন import
from threading import Thread
from flask import Flask, request, jsonify
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError, PasswordHashInvalidError, FloodWaitError
)

# ----------------------
# Configuration
# ----------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")

# Pyrogram Client Initialization (API_ID/HASH added to prevent new AttributeError)
# NOTE: The API_ID and API_HASH here are the HOST's/YOUR credentials, needed for the bot to run.
bot = Client(
    "webhook_bot", 
    bot_token=BOT_TOKEN,
    api_id=int(API_ID) if API_ID and API_ID.isdigit() else 12345,  # Dummy fallback for safety
    api_hash=API_HASH or "dummyhash"
)

app = Flask(__name__)

# ----------------------
# User session tracking
# ----------------------
user_steps = {}
user_temp_data = {}

def cleanup_user(user_id):
    """Cleans up user's temporary state data."""
    if user_id in user_steps: del user_steps[user_id]
    if user_id in user_temp_data: del user_temp_data[user_id]

async def timeout_cleanup(user_id, delay=300):
    """Sends timeout message and cleans up state after delay."""
    await asyncio.sleep(delay)
    if user_id in user_steps:
        # Check if the user is still waiting for input (step 'waiting_for_code' or 'waiting_for_2fa_password')
        current_step = user_steps.get(user_id)
        if current_step in ["waiting_for_code", "waiting_for_2fa_password"]:
            await bot.send_message(user_id, "OTP/Password expired. Please start again with `/generate`.")
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
    # Acknowledge the callback query instantly
    await query.answer()

    # Check if the user is in the correct step
    if user_steps.get(user_id) != "waiting_for_lib_choice":
        await query.message.edit("অনুগ্রহ করে `/generate` দিয়ে আবার শুরু করুন।")
        cleanup_user(user_id)
        return

    lib_choice = query.data.split("_")[1]
    user_temp_data[user_id] = {"library": lib_choice}
    user_steps[user_id] = "waiting_for_api_id"
    await query.message.edit(f"**{lib_choice.capitalize()}** বেছে নেওয়া হলো। এখন আপনার **API\_ID** দিন:")

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
        await message.reply_text("এখন আপনার **API\_HASH** দিন:")

    elif step == "waiting_for_api_hash":
        user_temp_data[user_id]["api_hash"] = text
        user_steps[user_id] = "waiting_for_phone"
        await message.reply_text("এখন আপনার **ফোন নাম্বার** দিন (+880...) ফরম্যাটে:")

    elif step == "waiting_for_phone":
        phone = text
        # Basic phone number validation
        if not phone.startswith("+") or len(phone) < 10:
            await message.reply_text("ভুল ফোন নাম্বার। +880... ফরম্যাটে দিন।")
            return
        
        user_temp_data[user_id]["phone_number"] = phone
        lib = user_temp_data[user_id]["library"]
        
        # Start the appropriate sign-in process
        user_steps[user_id] = "waiting_for_code"
        if lib == "pyrogram":
            asyncio.create_task(send_pyrogram_code(user_id))
        else:
            asyncio.create_task(send_telethon_code(user_id))

    elif step == "waiting_for_code":
        user_temp_data[user_id]["otp_code"] = text
        lib = user_temp_data[user_id]["library"]
        
        if lib == "pyrogram":
            asyncio.create_task(sign_in_pyrogram(user_id))
        else:
            asyncio.create_task(sign_in_telethon(user_id))

    elif step == "waiting_for_2fa_password":
        user_temp_data[user_id]["password"] = text
        lib = user_temp_data[user_id]["library"]

        if lib == "pyrogram":
            asyncio.create_task(check_pyrogram_password(user_id))
        else:
            asyncio.create_task(check_telethon_password(user_id))
    else:
        await message.reply_text("প্রক্রিয়া শুরু করতে `/generate` দিন।")

# ----------------------
# Pyrogram OTP Functions
# ----------------------
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeInvalid, PasswordHashInvalid, FloodWait

async def send_pyrogram_code(user_id):
    data = user_temp_data[user_id]
    
    # Client for session generation (uses USER's API_ID and API_HASH)
    client = Client(":memory:", api_id=data["api_id"], api_hash=data["api_hash"], in_memory=True)
    data["temp_client"] = client
    
    try:
        await client.connect()
        sent = await client.send_code(data["phone_number"])
        data["sent_code_info"] = sent
        await bot.send_message(user_id, "OTP পাঠানো হলো। ৫ মিনিটের মধ্যে submit করুন।")
        asyncio.create_task(timeout_cleanup(user_id, 300))
    except FloodWait as e:
        await bot.send_message(user_id, f"⚠️ Flood Wait Error: অনুগ্রহ করে **{e.value} সেকেন্ড** পরে আবার চেষ্টা করুন।")
        cleanup_user(user_id)
    except Exception as e:
        await bot.send_message(user_id, f"OTP পাঠাতে সমস্যা: `{e}`")
        cleanup_user(user_id)

async def sign_in_pyrogram(user_id):
    data = user_temp_data[user_id]
    client = data.get("temp_client")

    if not client:
        await bot.send_message(user_id, "❌ ত্রুটি: ক্লায়েন্ট অবজেক্ট পাওয়া যায়নি। অনুগ্রহ করে `/generate` দিয়ে আবার শুরু করুন।")
        cleanup_user(user_id)
        return

    try:
        await client.sign_in(
            data["phone_number"], 
            data["sent_code_info"].phone_code_hash, 
            data["otp_code"]
        )
        session_str = await client.export_session_string()
        await bot.send_message(user_id,f"✅ **Pyrogram Session** তৈরি হলো:\n\n```python\n{session_str}\n```", parse_mode="markdown")
        cleanup_user(user_id)
    except SessionPasswordNeeded:
        user_steps[user_id] = "waiting_for_2fa_password"
        await bot.send_message(user_id, "২FA পাসওয়ার্ড প্রয়োজন। আপনার ক্লাউড পাসওয়ার্ড দিন।")
    except PhoneCodeInvalid:
        await bot.send_message(user_id, "❌ ভুল OTP। অনুগ্রহ করে `/generate` দিয়ে আবার শুরু করুন।")
        cleanup_user(user_id)
    except Exception as e:
        await bot.send_message(user_id, f"❌ Sign in error: `{e}`")
        cleanup_user(user_id)
    finally:
        # We disconnect here only if the process is finished/failed before 2FA is needed
        if user_steps.get(user_id) != "waiting_for_2fa_password":
            await client.disconnect()

async def check_pyrogram_password(user_id):
    data = user_temp_data[user_id]
    client = data.get("temp_client")

    if not client:
        await bot.send_message(user_id, "❌ ত্রুটি: ক্লায়েন্ট অবজেক্ট পাওয়া যায়নি। অনুগ্রহ করে `/generate` দিয়ে আবার শুরু করুন।")
        cleanup_user(user_id)
        return

    try:
        await client.check_password(data["password"])
        session_str = await client.export_session_string()
        await bot.send_message(user_id,f"✅ **Pyrogram Session** তৈরি হলো:\n\n```python\n{session_str}\n```", parse_mode="markdown")
    except PasswordHashInvalid:
        await bot.send_message(user_id, "❌ ভুল পাসওয়ার্ড। অনুগ্রহ করে `/generate` দিয়ে আবার শুরু করুন।")
    except Exception as e:
        await bot.send_message(user_id, f"❌ Password error: `{e}`")
    finally:
        cleanup_user(user_id)
        await client.disconnect()

# ----------------------
# Telethon OTP Functions
# ----------------------
async def send_telethon_code(user_id):
    data = user_temp_data[user_id]
    
    # Client for session generation (uses USER's API_ID and API_HASH)
    client = TelegramClient(
        StringSession(), 
        api_id=data["api_id"], 
        api_hash=data["api_hash"],
        # Telethon client should use a different loop than pyrogram's
        loop=asyncio.get_event_loop() 
    )
    data["temp_client"] = client
    
    try:
        await client.connect()
        await client.send_code_request(data["phone_number"])
        await bot.send_message(user_id, "OTP পাঠানো হলো। ৫ মিনিটের মধ্যে submit করুন।")
        asyncio.create_task(timeout_cleanup(user_id, 300))
    except FloodWaitError as e:
        await bot.send_message(user_id, f"⚠️ Flood Wait Error: অনুগ্রহ করে **{e.seconds} সেকেন্ড** পরে আবার চেষ্টা করুন।")
        cleanup_user(user_id)
    except Exception as e:
        await bot.send_message(user_id, f"OTP পাঠাতে সমস্যা: `{e}`")
        cleanup_user(user_id)

async def sign_in_telethon(user_id):
    data = user_temp_data[user_id]
    client = data.get("temp_client")
    
    if not client:
        await bot.send_message(user_id, "❌ ত্রুটি: ক্লায়েন্ট অবজেক্ট পাওয়া যায়নি। অনুগ্রহ করে `/generate` দিয়ে আবার শুরু করুন।")
        cleanup_user(user_id)
        return

    try:
        await client.sign_in(data["phone_number"], data["otp_code"])
        session_str = client.session.save()
        await bot.send_message(user_id,f"✅ **Telethon Session** তৈরি হলো:\n\n```python\n{session_str}\n```", parse_mode="markdown")
        cleanup_user(user_id)
    except SessionPasswordNeededError:
        user_steps[user_id] = "waiting_for_2fa_password"
        await bot.send_message(user_id, "২FA পাসওয়ার্ড প্রয়োজন। আপনার ক্লাউড পাসওয়ার্ড দিন।")
    except PhoneCodeInvalidError:
        await bot.send_message(user_id, "❌ ভুল OTP। অনুগ্রহ করে `/generate` দিয়ে আবার শুরু করুন।")
        cleanup_user(user_id)
    except Exception as e:
        await bot.send_message(user_id, f"❌ Sign in error: `{e}`")
        cleanup_user(user_id)
    finally:
        if user_steps.get(user_id) != "waiting_for_2fa_password":
            await client.disconnect()

async def check_telethon_password(user_id):
    data = user_temp_data[user_id]
    client = data.get("temp_client")

    if not client:
        await bot.send_message(user_id, "❌ ত্রুটি: ক্লায়েন্ট অবজেক্ট পাওয়া যায়নি। অনুগ্রহ করে `/generate` দিয়ে আবার শুরু করুন।")
        cleanup_user(user_id)
        return

    try:
        await client.sign_in(password=data["password"])
        session_str = client.session.save()
        await bot.send_message(user_id,f"✅ **Telethon Session** তৈরি হলো:\n\n```python\n{session_str}\n```", parse_mode="markdown")
    except PasswordHashInvalidError:
        await bot.send_message(user_id, "❌ ভুল পাসওয়ার্ড। অনুগ্রহ করে `/generate` দিয়ে আবার শুরু করুন।")
    except Exception as e:
        await bot.send_message(user_id, f"❌ Password error: `{e}`")
    finally:
        cleanup_user(user_id)
        await client.disconnect()

# ----------------------
# Flask Webhook route
# ----------------------
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    """Handles incoming Telegram updates and processes them asynchronously."""
    update = request.get_json(silent=True)
    if update:
        # Use asyncio.ensure_future to schedule bot processing without blocking Flask
        asyncio.ensure_future(bot.process_new_updates([update]))
    return jsonify({"status": "ok"})


# ----------------------
# Webhook Setup Function (Sync) - Fixes AttributeError
# ----------------------
def set_webhook_sync(url):
    """Sets the Telegram webhook using a synchronous HTTP request."""
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN is not set. Cannot set webhook.")
        return

    webhook_url = f"{url}/{BOT_TOKEN}"
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    
    # Send the synchronous request
    response = requests.post(api_url, data={"url": webhook_url})

    if response.status_code == 200 and response.json().get('ok'):
        print(f"✅ Webhook successfully set to: {webhook_url}")
    else:
        print(f"❌ Failed to set webhook. Status: {response.status_code}, Response: {response.text}")


# ----------------------
# Run Flask + Bot
# ----------------------
if __name__ == "__main__":
    # 1. Setup nested asyncio (necessary for Pyrogram/Telethon in a threaded environment)
    import nest_asyncio
    nest_asyncio.apply()

    url = os.environ.get("RENDER_EXTERNAL_URL")
    port = int(os.environ.get("PORT", 5000))
    
    # 2. Set webhook synchronously before starting the server
    if url:
        set_webhook_sync(url)
    else:
        print("⚠️ RENDER_EXTERNAL_URL not found. Webhook will not be set. Running locally.")
    
    # 3. Start the Flask server in a separate thread
    Thread(target=lambda: app.run(host="0.0.0.0", port=port)).start()
    
    # 4. Start the Pyrogram client (which runs in the main thread's asyncio loop)
    print("🚀 Starting Pyrogram Client...")
    bot.run()

