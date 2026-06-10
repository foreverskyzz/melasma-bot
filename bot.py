import os
import sys
from datetime import datetime
import pytz
import requests
from telegram.ext import Updater, CommandHandler, MessageHandler, CallbackQueryHandler, Filters
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

print("Starting Hyperbeam Melasma Bot...")

# Environment variables
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip('/')
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

if not all([SUPABASE_URL, SUPABASE_KEY, BOT_TOKEN]):
    print("ERROR: Missing environment variables")
    sys.exit(1)

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

def supabase_select(table, filters=None):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    params = {}
    if filters:
        for col, val in filters.items():
            params[col] = f"eq.{val}"
    try:
        r = requests.get(url, headers=HEADERS, params=params)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"Select error: {e}")
        return []

def supabase_insert(table, data):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    try:
        r = requests.post(url, headers=HEADERS, json=data)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"Insert error: {e}")
        return False

def supabase_upload_storage(bucket, file_name, file_bytes):
    url = f"{SUPABASE_URL}/storage/v1/object/{bucket}/{file_name}"
    headers = {**HEADERS, "Content-Type": "image/jpeg"}
    try:
        r = requests.post(url, headers=headers, data=file_bytes)
        r.raise_for_status()
        return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{file_name}"
    except Exception as e:
        print(f"Upload error: {e}")
        return None

def start(update, context):
    chat_id = update.effective_chat.id
    name = update.effective_user.first_name
    user = supabase_select("users", {"telegram_chat_id": chat_id})
    if user:
        # Calculate current day number
        local_tz = pytz.timezone('Asia/Shanghai')
        now = datetime.now(local_tz)
        start_date_str = user[0].get('start_date')
        if start_date_str:
            start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00')).astimezone(local_tz)
            days_since_start = (now.date() - start_date.date()).days
            day_number = days_since_start + 1
            if day_number < 1:
                day_number = 1
        else:
            day_number = 1
        update.message.reply_text(
            f"Welcome back! Ready to send your photos for Day {day_number}? Just tap the upload button!"
        )
    else:
        update.message.reply_text(
            f"Hello {name}, thank you for using Hyperbeam (tentative name for our fluocinolone acetonide, hydroquinone, and tretinoin cream).\n\n"
            "Welcome onboard in documenting your journey with us in erasing dark spots (melasma).\n\n"
            "Choose 5 places where you have dark spots and send their names to me (comma separated).\n"
            "Example: Left cheek, Right cheek, Forehead, Upper lip, Chin"
        )
        context.user_data['awaiting_sites'] = True

def handle_message(update, context):
    chat_id = update.effective_chat.id
    text = update.message.text
    if context.user_data.get('awaiting_sites'):
        site_names = [s.strip() for s in text.split(',')]
        if len(site_names) != 5:
            update.message.reply_text("Need exactly 5 names, separated by commas.")
            return
        supabase_insert("users", {
            'telegram_chat_id': chat_id,
            'site_names': site_names,
            'name': update.effective_user.full_name
        })
        del context.user_data['awaiting_sites']
        update.message.reply_text(
            "Sites saved! Tomorrow at 8am your local time will be Day 1 of your journey.\n\n"
            "Send me a photo, then tap which spot it's for."
        )
    else:
        update.message.reply_text("Send /start to begin.")

def handle_photo(update, context):
    chat_id = update.effective_chat.id
    user = supabase_select("users", {"telegram_chat_id": chat_id})
    if not user:
        update.message.reply_text("Please /start first.")
        return
    user = user[0]
    site_names = user['site_names']
    photo_file = update.message.photo[-1].get_file()
    file_name = f"{chat_id}_{datetime.utcnow().timestamp()}.jpg"
    photo_bytes = photo_file.download_as_bytearray()
    photo_url = supabase_upload_storage("melasma-photos", file_name, photo_bytes)
    if not photo_url:
        update.message.reply_text("Upload failed. Try again.")
        return
    # Store photo_url in user_data temporarily
    context.user_data['temp_photo_url'] = photo_url
    keyboard = [[InlineKeyboardButton(name, callback_data=str(idx))] for idx, name in enumerate(site_names)]
    update.message.reply_text("Which spot is this?", reply_markup=InlineKeyboardMarkup(keyboard))

def site_callback(update, context):
    query = update.callback_query
    query.answer()
    site_idx = int(query.data)
    photo_url = context.user_data.get('temp_photo_url')
    if not photo_url:
        query.edit_message_text("Error: no photo found. Please send again.")
        return
    chat_id = query.message.chat.id
    user = supabase_select("users", {"telegram_chat_id": chat_id})
    if user:
        supabase_insert("photos", {
            'user_id': user[0]['id'],
            'site_index': site_idx,
            'photo_url': photo_url
        })
        site_name = user[0]['site_names'][site_idx]
        query.edit_message_text(f"Saved photo for {site_name}. You can send another or stop for today.")
        # Clear temporary data
        context.user_data.pop('temp_photo_url', None)
    else:
        query.edit_message_text("Error. Please /start again.")

def check_reminders(context):
    local_tz = pytz.timezone('Asia/Shanghai')
    now = datetime.now(local_tz)
    if now.hour < 8 or now.hour >= 22:
        return
    users = supabase_select("users")
    for user in users:
        chat_id = user['telegram_chat_id']
        user_id = user['id']
        # Get or set start_date
        start_date_str = user.get('start_date')
        if start_date_str:
            start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00')).astimezone(local_tz)
        else:
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
            supabase_insert("users", {'id': user_id, 'start_date': start_date.isoformat()})
        days_since_start = (now.date() - start_date.date()).days
        day_number = days_since_start + 1
        if day_number < 1:
            day_number = 1
        # Check if already uploaded today
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = today_start.astimezone(pytz.UTC)
        photos = supabase_select("photos", {"user_id": user_id})
        has_photo_today = any(
            datetime.fromisoformat(p['uploaded_at'].replace('Z', '+00:00')) >= today_start_utc
            for p in photos
        )
        if not has_photo_today:
            message = f"Day {day_number} in fighting dark spots with Hyperbeam, let's upload your progress (pictures of the same five melasma spots)."
            context.bot.send_message(chat_id, message)

def main():
    print("Bot is now polling...")
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    dp.add_handler(MessageHandler(Filters.photo, handle_photo))
    dp.add_handler(CallbackQueryHandler(site_callback))
    jq = updater.job_queue
    if jq:
        jq.run_repeating(check_reminders, interval=1200, first=0)
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
