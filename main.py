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

# Environment Variables
BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")

# Client Initialization
# Note: Pyrogram now requires API_ID and API_HASH even for bot tokens
try:
    # API_ID is now passed directly as Pyrogram handles the type conversion
    # Ensure a proper name is used for the session file (though unused in webhook mode)
    bot = Client("session_generator", bot_token=BOT_TOKEN, api_id=API_ID, api_hash=API_HASH)
except Exception as e:
    # Print error for debugging but continue with a fallback client
    print(f"Error initializing Pyrogram Client with API credentials: {e}")
    bot = Client("session_generator", bot_token=BOT_TOKEN) 

app = Flask(__name__)

# ----------------------
# User session tracking
# ----------------------
user_steps = {}
user_temp_data = {}

def cleanup_user(user_id):
    """Removes temporary data for the user."""
    # Ensure temporary client objects are gracefully disconnected before removal
    if user_id in user_temp_data and "temp_client" in user_temp_data[user_id]:
        client = user_temp_data[user_id]["temp_client"]
        # Since we are using an async library with a sync function, we use asyncio.run
        # NOTE: This is generally risky but necessary in a cleanup function 
        # called from various contexts in this specific Flask/Pyrogram setup.
        try:
            asyncio.run(client.disconnect())
        except Exception:
            pass # Ignore disconnect errors on cleanup
    
    if user_id in user_steps: del user_steps[user_id]
    if user_id in user_temp_data: del user_temp_data[user_id]

async def timeout_cleanup(user_id, delay=300):
    """Cleans up user data after a timeout."""
    await asyncio.sleep(delay)
    if user_id in user_steps:
        try:
            await bot.send_message(user_id, "OTPexpired। `/generate` দিয়ে আবার শুরু করুন।")
        except Exception:
            pass # Ignore if user blocked bot
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
    # Check if the user is in the correct step to prevent hijacking
    if user_steps.get(user_id) != "waiting_for_lib_choice":
        await query.answer("প্রক্রিয়া শুরু করতে /generate দিন।", show_alert=True)
        await query.message.delete()
        return

    lib_choice = query.data.split("_")[1]
    user_temp_data[user_id] = {"library": lib_choice}
    user_steps[user_id] = "waiting_for_api_id"
    # Escaping underscores to prevent potential MarkdownV2 parse issues
    await query.message.edit_text(f"{lib_choice.capitalize()} বেছে নেওয়া হলো। এখন আপনার API\_ID দিন:", parse_mode="markdown")

@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_handler(_, message):
    user_id = message.from_user.id
    if user_id in user_steps:
        cleanup_user(user_id)
        await message.reply_text("প্রক্রিয়া বাতিল করা হলো।")
    else:
        await message.reply_text("বর্তমানে কোনো প্রক্রিয়া চলছে না।")

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
        # Simple validation for phone number format
        if not phone.startswith("+") or len(phone)<10 or any(c.isalpha() for c in phone):
            await message.reply_text("ভুল ফোন নাম্বার। +880... ফরম্যাটে দিন।")
            return
        user_temp_data[user_id]["phone_number"] = phone
        lib = user_temp_data[user_id]["library"]
        
        # Start the code sending process asynchronously
        if lib == "pyrogram":
            user_steps[user_id] = "waiting_for_code"
            asyncio.create_task(send_pyrogram_code(user_id))
        else:
            user_steps[user_id] = "waiting_for_code"
            asyncio.create_task(send_telethon_code(user_id))

    elif step == "waiting_for_code":
        user_temp_data[user_id]["otp_code"] = text
        lib = user_temp_data[user_id]["library"]
        # Start the sign-in process asynchronously
        if lib == "pyrogram":
            asyncio.create_task(sign_in_pyrogram(user_id))
        else:
            asyncio.create_task(sign_in_telethon(user_id))

    elif step == "waiting_for_2fa_password":
        user_temp_data[user_id]["password"] = text
        lib = user_temp_data[user_id]["library"]
        # Start the password check process asynchronously
        if lib == "pyrogram":
            asyncio.create_task(check_pyrogram_password(user_id))
        else:
            asyncio.create_task(check_telethon_password(user_id))
    else:
        # Default message if user sends a message but isn't in a process
        await message.reply_text("প্রক্রিয়া শুরু করতে `/generate` দিন।")

# ----------------------
# Pyrogram OTP and Session Logic
# ----------------------
async def send_pyrogram_code(user_id):
    data = user_temp_data[user_id]
    # Use a temporary in-memory client for the session
    client = Client(name=f":memory:{user_id}", api_id=data["api_id"], api_hash=data["api_hash"])
    
    try:
        await client.connect()
        data["temp_client"] = client
        sent = await client.send_code(data["phone_number"])
        data["sent_code_info"] = sent
        await bot.send_message(user_id, "OTP পাঠানো হলো। 5 মিনিটের মধ্যে submit করুন।")
        asyncio.create_task(timeout_cleanup(user_id,300))
    except Exception as e:
        await bot.send_message(user_id, f"❌ OTP পাঠাতে সমস্যা: {e}")
        cleanup_user(user_id)

async def sign_in_pyrogram(user_id):
    data = user_temp_data.get(user_id)
    if not data or "temp_client" not in data:
        await bot.send_message(user_id,"❌ সেশন মেয়াদ উত্তীর্ণ। আবার `/generate` দিন।")
        return cleanup_user(user_id)
        
    client = data["temp_client"]
    try:
        await client.sign_in(data["phone_number"], data["sent_code_info"].phone_code_hash, data["otp_code"])
        session_str = await client.export_session_string()
        # Sending code block 
        await bot.send_message(user_id,f"✅ Session তৈরি হলো:\n\n```python\n{session_str}\n```")
    except SessionPasswordNeeded:
        user_steps[user_id] = "waiting_for_2fa_password"
        await bot.send_message(user_id,"2FA পাসওয়ার্ড দিন।")
        return # Do not cleanup yet
    except PhoneCodeInvalid:
        await bot.send_message(user_id,"❌ ভুল OTP। আবার `/generate` দিন।")
    except Exception as e:
        await bot.send_message(user_id,f"❌ Sign in error: {e}")
    finally:
        # Cleanup after success, OTP error, or general error (but not 2FA)
        if user_steps.get(user_id) != "waiting_for_2fa_password":
            cleanup_user(user_id)

async def check_pyrogram_password(user_id):
    data = user_temp_data.get(user_id)
    if not data or "temp_client" not in data:
        await bot.send_message(user_id,"❌ সেশন মেয়াদ উত্তীর্ণ। আবার `/generate` দিন।")
        return cleanup_user(user_id)
        
    client = data["temp_client"]
    try:
        await client.check_password(data["password"])
        session_str = await client.export_session_string()
        # Sending code block 
        await bot.send_message(user_id,f"✅ Session তৈরি হলো:\n\n```python\n{session_str}\n```")
    except Exception as e:
        await bot.send_message(user_id,f"❌ Password error: {e}")
    finally:
        cleanup_user(user_id)

# ----------------------
# Telethon OTP and Session Logic
# ----------------------
async def send_telethon_code(user_id):
    data = user_temp_data[user_id]
    # Use StringSession for in-memory session
    client = TelegramClient(StringSession(), api_id=data["api_id"], api_hash=data["api_hash"])
    
    try:
        await client.connect()
        data["temp_client"] = client
        await client.send_code_request(data["phone_number"])
        await bot.send_message(user_id, "OTP পাঠানো হলো। 5 মিনিটের মধ্যে submit করুন।")
        asyncio.create_task(timeout_cleanup(user_id,300))
    except Exception as e:
        await bot.send_message(user_id,f"❌ OTP পাঠাতে সমস্যা: {e}")
        cleanup_user(user_id)

async def sign_in_telethon(user_id):
    data = user_temp_data.get(user_id)
    if not data or "temp_client" not in data:
        await bot.send_message(user_id,"❌ সেশন মেয়াদ উত্তীর্ণ। আবার `/generate` দিন।")
        return cleanup_user(user_id)
        
    client = data["temp_client"]
    try:
        await client.sign_in(data["phone_number"], data["otp_code"])
        session_str = client.session.save()
        # Sending code block 
        await bot.send_message(user_id,f"✅ Session তৈরি হলো:\n\n```python\n{session_str}\n```")
    except SessionPasswordNeededError:
        user_steps[user_id] = "waiting_for_2fa_password"
        await bot.send_message(user_id,"2FA পাসওয়ার্ড দিন।")
        return # Do not cleanup yet
    except PhoneCodeInvalidError:
        await bot.send_message(user_id,"❌ ভুল OTP। আবার `/generate` দিন।")
    except Exception as e:
        await bot.send_message(user_id,f"❌ Sign in error: {e}")
    finally:
        # Cleanup after success, OTP error, or general error (but not 2FA)
        if user_steps.get(user_id) != "waiting_for_2fa_password":
            cleanup_user(user_id)

async def check_telethon_password(user_id):
    data = user_temp_data.get(user_id)
    if not data or "temp_client" not in data:
        await bot.send_message(user_id,"❌ সেশন মেয়াদ উত্তীর্ণ। আবার `/generate` দিন।")
        return cleanup_user(user_id)
        
    client = data["temp_client"]
    try:
        await client.sign_in(password=data["password"])
        session_str = client.session.save()
        # Sending code block 
        await bot.send_message(user_id,f"✅ Session তৈরি হলো:\n\n```python\n{session_str}\n```")
    except PasswordHashInvalidError:
        await bot.send_message(user_id,"❌ ভুল পাসওয়ার্ড। `/generate` দিয়ে আবার শুরু করুন।")
    except Exception as e:
        await bot.send_message(user_id,f"❌ Password error: {e}")
    finally:
        cleanup_user(user_id)

# ----------------------
# Flask Webhook route
# ----------------------
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
async def webhook():
    # Attempt to get JSON data
    try:
        # Use silent=True to handle bad JSON gracefully
        update = request.get_json(silent=True)
    except Exception as e:
        print(f"Error parsing JSON: {e}")
        update = None
        
    if update:
        # Pass the update to Pyrogram's process_new_updates asynchronously
        # This is the core of Webhook-based handling.
        asyncio.create_task(bot.process_new_updates([update]))
        
    return jsonify({"status": "ok"})

# ----------------------
# Set Webhook (Synchronous Fix)
# ----------------------
def set_webhook_sync():
    """Sets the webhook using a synchronous HTTP request."""
    try:
        # Use RENDER_EXTERNAL_URL or similar environment variable for the public URL
        url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("EXTERNAL_URL")
        if not url:
            print("❌ WARNING: EXTERNAL URL not found. Webhook will not be set.")
            return

        webhook_url = f"{url}/{BOT_TOKEN}"
        api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
        
        # We explicitly set `drop_pending_updates=True` to clear any old updates
        # that might be pending from a previous failed run.
        response = requests.post(api_url, json={'url': webhook_url, 'drop_pending_updates': True})
        
        if response.status_code == 200 and response.json().get('ok'):
            print(f"✅ Webhook set successfully to: {webhook_url}")
        else:
            print(f"❌ Webhook failed to set. Response: {response.text}")
            
    except Exception as e:
        print(f"❌ Error during webhook setup: {e}")

# ----------------------
# Run Flask + Bot
# ----------------------
if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    
    # 1. Set Webhook Synchronously
    set_webhook_sync()

    # 2. Start Pyrogram Client (without Long Polling)
    # bot.start() is necessary to load handlers and establish connection status
    print("🚀 Starting Pyrogram Client (Webhook Mode)...")
    try:
        # Start the client asynchronously
        asyncio.run(bot.start())
        print("✅ Pyrogram Client started.")
    except Exception as e:
        print(f"❌ Error starting Pyrogram Client: {e}")
        # If bot.start fails, the bot won't work, but Flask can still start for debugging

    # 3. Start Flask Server in a separate Thread
    # This runs the webserver that receives updates via webhook.
    print("🌐 Starting Flask Webserver...")
    # NOTE: The app.run call is sync, so it must be in a thread or the main process.
    # We use a thread to keep the main thread available for cleanups/other tasks.
    Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))).start()
    
    # *** গুরুত্বপূর্ণ পরিবর্তন: bot.run() কলটি বাদ দেওয়া হয়েছে! ***
    # এই কলটিই লং পোলিং শুরু করত, যা ওয়েবহুকের সাথে সংঘর্ষ তৈরি করছিল।
