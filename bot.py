import time
from datetime import datetime
import pytz
import requests
from telegram.ext import Updater, CommandHandler, MessageHandler, CallbackQueryHandler, Filters
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import os

# Supabase config from environment variables
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

def supabase_insert(table, data):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    requests.post(url, headers=HEADERS, json=data).raise_for_status()

def supabase_select(table, filters=None):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    params = {f"{col}": f"eq.{val}" for col, val in (filters or {}).items()}
    resp = requests.get(url, headers=HEADERS, params=params)
    resp.raise_for_status()
    return resp.json()

def supabase_upload_storage(bucket, file_name, file_bytes):
    url = f"{SUPABASE_URL}/storage/v1/object/{bucket}/{file_name}"
    headers = {**HEADERS, "Content-Type": "image/jpeg"}
    requests.post(url, headers=headers, data=file_bytes).raise_for_status()
    return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{file_name}"

def start(update, context):
    chat_id = update.effective_chat.id
    user = supabase_select("users", {"telegram_chat_id": chat_id})
    if user:
        update.message.reply_text("Welcome back! Send a photo of a melasma spot.")
    else:
        update.message.reply_text(
            "Hi! This bot tracks your melasma treatment.\n\n"
            "First, send me the names of your 5 spots as a list separated by commas.\n"
            "Example: Left cheek, Right cheek, Forehead, Upper lip, Chin"
        )
        context.user_data['awaiting_sites'] = True

def handle_message(update, context):
    chat_id = update.effective_chat.id
    text = update.message.text
    if context.user_data.get('awaiting_sites'):
        site_names = [s.strip() for s in text.split(',')]
        if len(site_names) != 5:
            update.message.reply_text("Please enter exactly 5 names separated by commas.")
            return
        supabase_insert("users", {
            'telegram_chat_id': chat_id,
            'site_names': site_names,
            'name': update.effective_user.full_name
        })
        del context.user_data['awaiting_sites']
        update.message.reply_text(
            "Got it. From tomorrow at 8am UTC, I'll remind you daily.\n"
            "Just send a photo and tap which spot it's for."
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
    keyboard = [[InlineKeyboardButton(name, callback_data=f"site_{idx}_{photo_url}")] for idx, name in enumerate(site_names)]
    update.message.reply_text("Thanks. Which spot is this?", reply_markup=InlineKeyboardMarkup(keyboard))

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
        query.edit_message_text(f"Saved photo for {site_name}. Send another or stop for today.")
    else:
        query.edit_message_text("Error. Please /start again.")

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
    updater = Updater(os.environ["BOT_TOKEN"], use_context=True)
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
