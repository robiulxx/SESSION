import os
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import (
    SessionPasswordNeeded, PhoneCodeInvalid, PasswordHashInvalid, PhoneNumberInvalid,
    FloodWait
)
from telethon.sessions import StringSession
from telethon.sync import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError, PasswordKeyInvalidError, PhoneNumberInvalidError,
    FloodWaitError
)

# --- বট এর কনফিগারেশন ---
# এই তথ্যগুলো অবশ্যই Environment Variables হিসেবে সেট করবেন
# যেমন: Render, Railway, Heroku-তে ENVIRONMENT VARIABLES সেকশনে যোগ করবেন।
# স্থানীয়ভাবে চালাতে হলে .env ফাইল ব্যবহার করতে পারেন (python-dotenv লাইব্রেরি দিয়ে)।
API_ID = int(os.environ.get("API_ID", "YOUR_API_ID")) # my.telegram.org থেকে alın
API_HASH = os.environ.get("API_HASH", "YOUR_API_HASH") # my.telegram.org থেকে alın
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN") # @BotFather থেকে alın

# বট ক্লায়েন্ট
bot = Client("session_generator_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ব্যবহারকারীর ডেটা সংরক্ষণের জন্য ডিকশনারি
# এটি একাধিক ব্যবহারকারী একই সময়ে সেশন জেনারেট করলে ডেটা মিক্সআপ হওয়া থেকে বাঁচাবে।
user_steps = {} # {user_id: step_number}
user_temp_data = {} # {user_id: {phone_number: ..., sent_code_info: ...}}

# --- কমান্ড হ্যান্ডলার ---

@bot.on_message(filters.command("start") & filters.private)
async def start_handler(_, message: Message):
    """/start কমান্ডের উত্তর দেয়"""
    await message.reply_text(
        "নমস্কার! আমি একটি টেলিগ্রাম স্ট্রিং সেশন জেনারেটর বট।\n\n"
        "আপনার Pyrogram বা Telethon স্ট্রিং সেশন তৈরি করতে `/generate` কমান্ডটি ব্যবহার করুন।\n\n"
        "**সতর্কতা:** আপনার কোনো তথ্যই আমি সংরক্ষণ করি না। এটি শুধুমাত্র একটি টুল।",
        quote=True
    )

@bot.on_message(filters.command("generate") & filters.private)
async def generate_command_handler(_, message: Message):
    """সেশন তৈরির অপশন দেখায়"""
    user_id = message.from_user.id
    
    # যদি কোনো প্রক্রিয়া চলমান থাকে, সেটি বাতিল করা হবে
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
    user_steps[user_id] = "choosing_session_type" # ব্যবহারকারী বর্তমানে কোন ধাপে আছে

@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_handler(_, message: Message):
    """সেশন তৈরির প্রক্রিয়া বাতিল করে"""
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
    """বাটন ক্লিকের পর কাজ করে"""
    session_type = callback_query.data.split("_")[1] # "pyrogram" বা "telethon"
    user_id = callback_query.from_user.id
    chat_id = callback_query.message.chat.id
    
    # নিশ্চিত করুন যে ব্যবহারকারী সঠিক ধাপে আছে
    if user_id not in user_steps or user_steps[user_id] != "choosing_session_type":
        await callback_query.answer("দয়া করে /generate কমান্ড ব্যবহার করে আবার শুরু করুন।", show_alert=True)
        return
        
    await callback_query.message.delete() # বাটনসহ মেসেজটি ডিলিট করে দেয়

    user_temp_data[user_id] = {"session_type": session_type}
    user_steps[user_id] = "waiting_for_phone_number"
    
    await client.send_message(
        chat_id, 
        f"আপনি **{session_type.capitalize()}** সেশন তৈরি করতে বেছে নিয়েছেন।\n\n"
        "দয়া করে আপনার টেলিগ্রাম অ্যাকাউন্টের ফোন নম্বরটি আন্তর্জাতিক ফরম্যাটে পাঠান (যেমন: `+8801712345678`)।"
    )

@bot.on_message(filters.text & filters.private & ~filters.command(["start", "generate", "cancel"]))
async def handle_user_input(client: Client, message: Message):
    """ব্যবহারকারীর ইনপুট (ফোন নম্বর, কোড, পাসওয়ার্ড) হ্যান্ডেল করে"""
    user_id = message.from_user.id
    current_step = user_steps.get(user_id)
    
    if current_step == "waiting_for_phone_number":
        user_temp_data[user_id]["phone_number"] = message.text
        user_steps[user_id] = "waiting_for_code"
        await send_code_and_ask_for_otp(client, message.chat.id, user_id)
    elif current_step == "waiting_for_code":
        user_temp_data[user_id]["otp_code"] = message.text
        user_steps[user_id] = "waiting_for_2fa_password" # সম্ভাব্য 2FA ধাপের জন্য
        await sign_in_and_generate_session(client, message.chat.id, user_id)
    elif current_step == "waiting_for_password":
        user_temp_data[user_id]["password"] = message.text
        del user_steps[user_id] # প্রক্রিয়া শেষ
        await check_password_and_generate_session(client, message.chat.id, user_id)
    else:
        # যদি ব্যবহারকারী ভুল ধাপে কোনো টেক্সট মেসেজ পাঠায়
        if user_id in user_steps: # যদি কোনো সক্রিয় প্রক্রিয়া থাকে কিন্তু মেসেজটি অপ্রত্যাশিত
            await message.reply_text("দয়া করে আপনার ইনপুটটি সঠিক ধাপে দিন অথবা `/cancel` লিখে প্রক্রিয়া বাতিল করুন।")
        else:
            await message.reply_text("আপনার সেশন তৈরি শুরু করতে `/generate` কমান্ড ব্যবহার করুন।")


async def send_code_and_ask_for_otp(client: Client, chat_id: int, user_id: int):
    """ভেরিফিকেশন কোড পাঠায় এবং OTP এর জন্য অপেক্ষা করে"""
    phone_number = user_temp_data[user_id]["phone_number"]
    session_type = user_temp_data[user_id]["session_type"]
    
    if session_type == "pyrogram":
        temp_client = Client(":memory:", api_id=API_ID, api_hash=API_HASH)
        user_temp_data[user_id]["temp_client"] = temp_client # ক্লায়েন্ট অবজেক্ট সংরক্ষণ
        try:
            await temp_client.connect()
            sent_code = await temp_client.send_code(phone_number)
            user_temp_data[user_id]["sent_code_info"] = sent_code
            await client.send_message(chat_id, "আপনার টেলিগ্রাম অ্যাপে একটি ভেরিফিকেশন কোড পাঠানো হয়েছে। দয়া করে সেই কোডটি এখানে দিন (যেমন: `12345`)।")
        except PhoneNumberInvalid:
            await client.send_message(chat_id, "ভুল ফোন নম্বর! দয়া করে সঠিক ফোন নম্বর দিয়ে আবার চেষ্টা করুন। `/generate`")
            del user_steps[user_id]
            del user_temp_data[user_id]
        except FloodWait as e:
            await client.send_message(chat_id, f"অনেক বেশি চেষ্টা করা হয়েছে। দয়া করে {e.value} সেকেন্ড পর আবার চেষ্টা করুন।")
            del user_steps[user_id]
            del user_temp_data[user_id]
        except Exception as e:
            await client.send_message(chat_id, f"কোড পাঠাতে একটি ত্রুটি হয়েছে: {e}")
            del user_steps[user_id]
            del user_temp_data[user_id]

    elif session_type == "telethon":
        # Telethon এর জন্য একটি ইন-মেমরি ক্লায়েন্ট
        temp_client = TelegramClient(StringSession(), API_ID, API_HASH)
        user_temp_data[user_id]["temp_client"] = temp_client # ক্লায়েন্ট অবজেক্ট সংরক্ষণ
        try:
            await temp_client.connect()
            await temp_client.send_code_request(phone_number)
            await client.send_message(chat_id, "আপনার টেলিগ্রাম অ্যাপে একটি ভেরিফিকেশন কোড পাঠানো হয়েছে। দয়া করে সেই কোডটি এখানে দিন (যেমন: `12345`)।")
        except PhoneNumberInvalidError:
            await client.send_message(chat_id, "ভুল ফোন নম্বর! দয়া করে সঠিক ফোন নম্বর দিয়ে আবার চেষ্টা করুন। `/generate`")
            del user_steps[user_id]
            del user_temp_data[user_id]
        except FloodWaitError as e:
            await client.send_message(chat_id, f"অনেক বেশি চেষ্টা করা হয়েছে। দয়া করে {e.value} সেকেন্ড পর আবার চেষ্টা করুন।")
            del user_steps[user_id]
            del user_temp_data[user_id]
        except Exception as e:
            await client.send_message(chat_id, f"কোড পাঠাতে একটি ত্রুটি হয়েছে: {e}")
            del user_steps[user_id]
            del user_temp_data[user_id]


async def sign_in_and_generate_session(client: Client, chat_id: int, user_id: int):
    """সাইন ইন করার চেষ্টা করে এবং সেশন তৈরি করে"""
    phone_number = user_temp_data[user_id]["phone_number"]
    otp_code = user_temp_data[user_id]["otp_code"]
    session_type = user_temp_data[user_id]["session_type"]
    temp_client = user_temp_data[user_id]["temp_client"] # সংরক্ষিত ক্লায়েন্ট অবজেক্ট
    
    if session_type == "pyrogram":
        try:
            await temp_client.sign_in(phone_number, user_temp_data[user_id]["sent_code_info"].phone_code_hash, otp_code)
            session_string = await temp_client.export_session_string()
            await client.send_message(
                chat_id,
                "আপনার Pyrogram V2 স্ট্রিং সেশন নিচে দেওয়া হলো:\n\n"
                f"```python\n{session_string}\n```\n\n"
                "**গুরুত্বপূর্ণ:** এটি কপি করে নিরাপদ স্থানে রাখুন এবং এই বার্তাটি ডিলিট করুন। কারো সাথে শেয়ার করবেন না!"
            )
        except SessionPasswordNeeded:
            user_steps[user_id] = "waiting_for_password"
            await client.send_message(chat_id, "আপনার অ্যাকাউন্টে দুই-ধাপ যাচাইকরণ (2FA) পাসওয়ার্ড চালু আছে। দয়া করে আপনার পাসওয়ার্ড দিন।")
            return # এখানে রিটার্ন করে কারণ পাসওয়ার্ডের জন্য আবার ইনপুট নিতে হবে
        except PhoneCodeInvalid:
            await client.send_message(chat_id, "ভুল কোড! দয়া করে সঠিক কোড দিন অথবা `/generate` কমান্ড ব্যবহার করে আবার চেষ্টা করুন।")
        except FloodWait as e:
            await client.send_message(chat_id, f"অনেক বেশি চেষ্টা করা হয়েছে। দয়া করে {e.value} সেকেন্ড পর আবার চেষ্টা করুন।")
        except Exception as e:
            await client.send_message(chat_id, f"সাইন ইন করতে একটি ত্রুটি হয়েছে: {e}")
        finally:
            del user_steps[user_id]
            del user_temp_data[user_id]
            await temp_client.disconnect()

    elif session_type == "telethon":
        try:
            await temp_client.sign_in(phone_number, otp_code)
            session_string = temp_client.session.save()
            await client.send_message(
                chat_id,
                "আপনার Telethon স্ট্রিং সেশন নিচে দেওয়া হলো:\n\n"
                f"```python\n{session_string}\n```\n\n"
                "**গুরুত্বপূর্ণ:** এটি কপি করে নিরাপদ স্থানে রাখুন এবং এই বার্তাটি ডিলিট করুন। কারো সাথে শেয়ার করবেন না!"
            )
        except SessionPasswordNeededError:
            user_steps[user_id] = "waiting_for_password"
            await client.send_message(chat_id, "আপনার অ্যাকাউন্টে দুই-ধাপ যাচাইকরণ (2FA) পাসওয়ার্ড চালু আছে। দয়া করে আপনার পাসওয়ার্ড দিন।")
            return # এখানে রিটার্ন করে কারণ পাসওয়ার্ডের জন্য আবার ইনপুট নিতে হবে
        except PhoneCodeInvalidError:
            await client.send_message(chat_id, "ভুল কোড! দয়া করে সঠিক কোড দিন অথবা `/generate` কমান্ড ব্যবহার করে আবার চেষ্টা করুন।")
        except FloodWaitError as e:
            await client.send_message(chat_id, f"অনেক বেশি চেষ্টা করা হয়েছে। দয়া করে {e.value} সেকেন্ড পর আবার চেষ্টা করুন।")
        except Exception as e:
            await client.send_message(chat_id, f"সাইন ইন করতে একটি ত্রুটি হয়েছে: {e}")
        finally:
            del user_steps[user_id]
            del user_temp_data[user_id]
            await temp_client.disconnect()


async def check_password_and_generate_session(client: Client, chat_id: int, user_id: int):
    """2FA পাসওয়ার্ড চেক করে এবং সেশন তৈরি করে"""
    password = user_temp_data[user_id]["password"]
    session_type = user_temp_data[user_id]["session_type"]
    temp_client = user_temp_data[user_id]["temp_client"]
    
    if session_type == "pyrogram":
        try:
            await temp_client.check_password(password)
            session_string = await temp_client.export_session_string()
            await client.send_message(
                chat_id,
                "আপনার Pyrogram V2 স্ট্রিং সেশন নিচে দেওয়া হলো:\n\n"
                f"```python\n{session_string}\n```\n\n"
                "**গুরুত্বপূর্ণ:** এটি কপি করে নিরাপদ স্থানে রাখুন এবং এই বার্তাটি ডিলিট করুন। কারো সাথে শেয়ার করবেন না!"
            )
        except PasswordHashInvalid:
            await client.send_message(chat_id, "ভুল পাসওয়ার্ড! প্রক্রিয়া বাতিল করা হয়েছে। `/generate` কমান্ড ব্যবহার করে আবার চেষ্টা করুন।")
        except FloodWait as e:
            await client.send_message(chat_id, f"অনেক বেশি চেষ্টা করা হয়েছে। দয়া করে {e.value} সেকেন্ড পর আবার চেষ্টা করুন।")
        except Exception as e:
            await client.send_message(chat_id, f"পাসওয়ার্ড যাচাই করতে একটি ত্রুটি হয়েছে: {e}")
        finally:
            del user_steps[user_id]
            del user_temp_data[user_id]
            await temp_client.disconnect()

    elif session_type == "telethon":
        try:
            await temp_client.sign_in(password=password)
            session_string = temp_client.session.save()
            await client.send_message(
                chat_id,
                "আপনার Telethon স্ট্রিং সেশন নিচে দেওয়া হলো:\n\n"
                f"```python\n{session_string}\n```\n\n"
                "**গুরুত্বপূর্ণ:** এটি কপি করে নিরাপদ স্থানে রাখুন এবং এই বার্তাটি ডিলিট করুন। কারো সাথে শেয়ার করবেন না!"
            )
        except PasswordKeyInvalidError:
            await client.send_message(chat_id, "ভুল পাসওয়ার্ড! প্রক্রিয়া বাতিল করা হয়েছে। `/generate` কমান্ড ব্যবহার করে আবার চেষ্টা করুন।")
        except FloodWaitError as e:
            await client.send_message(chat_id, f"অনেক বেশি চেষ্টা করা হয়েছে। দয়া করে {e.value} সেকেন্ড পর আবার চেষ্টা করুন।")
        except Exception as e:
            await client.send_message(chat_id, f"পাসওয়ার্ড যাচাই করতে একটি ত্রুটি হয়েছে: {e}")
        finally:
            del user_steps[user_id]
            del user_temp_data[user_id]
            await temp_client.disconnect()


# --- বট চালু করা ---
if __name__ == "__main__":
    print("বট চালু হচ্ছে...")
    bot.run() # Pyrogram স্বয়ংক্রিয়ভাবে start() এবং idle() ম্যানেজ করে
    print("বট সফলভাবে চালু হয়েছে!")
