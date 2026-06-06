import os
import sys
from datetime import datetime
import pytz
import requests
from telegram.ext import Updater, CommandHandler, MessageHandler, CallbackQueryHandler, Filters
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

print("Starting bot...")

# Check environment variables
required_vars = ["SUPABASE_URL", "SUPABASE_KEY", "BOT_TOKEN"]
missing = [v for v in required_vars if not os.environ.get(v)]
if missing:
    print(f"ERROR: Missing environment variables: {missing}")
    sys.exit(1)

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip('/')
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
BOT_TOKEN = os.environ["BOT_TOKEN"]

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

def supabase_insert(table, data):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    try:
        r = requests.post(url, headers=HEADERS, json=data)
        r.raise_for_status()
    except Exception as e:
        print(f"Insert error: {e}")

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
    user = supabase_select("users", {"telegram_chat_id": chat_id})
    if user:
        update.message.reply_text("Welcome back! Send a photo.")
    else:
        update.message.reply_text(
            "Hi! Send me the names of your 5 melasma spots (comma separated).\n"
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
        update.message.reply_text("Sites saved! Send a photo, then tap which spot.")
    else:
        update.message.reply_text("Send /start")

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
    keyboard = [[InlineKeyboardButton(name, callback_data=f"site_{idx}_{photo_url}")] for idx, name in enumerate(site_names)]
    update.message.reply_text("Which spot?", reply_markup=InlineKeyboardMarkup(keyboard))

def site_callback(update, context):
    query = update.callback_query
    query.answer()
    data = query.data
    parts = data.split('_')
    site_idx = int(parts[1])
    photo_url = '_'.join(parts[2:])
    chat_id = query.message.chat.id
    user = supabase_select("users", {"telegram_chat_id": chat_id})
    if user:
        supabase_insert("photos", {
            'user_id': user[0]['id'],
            'site_index': site_idx,
            'photo_url': photo_url
        })
        site_name = user[0]['site_names'][site_idx]
        query.edit_message_text(f"Saved {site_name}. You can send another.")
    else:
        query.edit_message_text("Error. /start again.")

def check_reminders(context):
    now = datetime.now(pytz.UTC)
    if now.hour < 8 or now.hour >= 22:
        return
    users = supabase_select("users")
    for user in users:
        chat_id = user['telegram_chat_id']
        user_id = user['id']
        today_start = datetime.now(pytz.UTC).replace(hour=0, minute=0, second=0)
        photos = supabase_select("photos", {"user_id": user_id})
        has_photo_today = any(
            datetime.fromisoformat(p['uploaded_at'].replace('Z', '+00:00')) >= today_start
            for p in photos
        )
        if not has_photo_today:
            context.bot.send_message(chat_id, "Time to document your melasma spots. Send a photo.")

def main():
    print("Bot starting...")
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
    print("Bot is polling...")
    updater.idle()

if __name__ == "__main__":
    main()
