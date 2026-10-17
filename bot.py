import asyncio
import json
import os
import threading
from datetime import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from supabase import create_client, Client
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

# East Africa Time (UTC+3) for accurate 7:00 AM and 9:30 PM reminders
EAT_TZ = ZoneInfo("Africa/Addis_Ababa")

supabase: Client | None = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# --- 1. Lightweight Health-Check Server for 24/7 Cloud Hosting (Render/Koyeb) ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"DentGuide Pro Bot is alive and running 24/7!")

    def log_message(self, format, *args):
        return  # Keep logs clean


def start_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


# --- 2. Helper Functions for Supabase Persistence ---
def upsert_user(chat_id: int, first_name: str = "", **fields):
    if not supabase:
        return
    payload = {"chat_id": chat_id, "first_name": first_name, **fields}
    try:
        supabase.table("dentguide_users").upsert(payload).execute()
    except Exception as e:
        print(f"Supabase upsert warning: {e}")


def schedule_user_reminders(job_queue, chat_id: int):
    for job in job_queue.get_jobs_by_name(str(chat_id)):
        job.schedule_removal()

    job_queue.run_daily(
        send_reminder,
        time=time(hour=7, minute=0, tzinfo=EAT_TZ),
        chat_id=chat_id,
        name=str(chat_id),
    )
    job_queue.run_daily(
        send_reminder,
        time=time(hour=21, minute=30, tzinfo=EAT_TZ),
        chat_id=chat_id,
        name=str(chat_id),
    )


async def restore_reminders_on_boot(app: Application) -> None:
    """Reloads all subscribed users from Supabase whenever the cloud server restarts."""
    if not supabase:
        return
    try:
        res = supabase.table("dentguide_users").select("chat_id").eq("remind_enabled", True).execute()
        for row in res.data:
            schedule_user_reminders(app.job_queue, row["chat_id"])
        print(f"✅ Restored daily reminders for {len(res.data)} users from Supabase.")
    except Exception as e:
        print(f"Could not restore reminders: {e}")


# --- 3. Bot Command & WebApp Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    upsert_user(update.effective_chat.id, user.first_name if user else "")

    keyboard = [
        [
            InlineKeyboardButton("🪥 🇬🇧 English", web_app=WebAppInfo(url=f"{WEBAPP_URL}?lang=en")),
            InlineKeyboardButton("🪥 🇪🇹 አማርኛ", web_app=WebAppInfo(url=f"{WEBAPP_URL}?lang=am")),
        ],
        [
            InlineKeyboardButton("🪥 🇪🇹 Afaan Oromoo", web_app=WebAppInfo(url=f"{WEBAPP_URL}?lang=om")),
            InlineKeyboardButton("🪥 🇪🇹 ትግርኛ", web_app=WebAppInfo(url=f"{WEBAPP_URL}?lang=ti")),
        ],
        [
            InlineKeyboardButton("🪥 🇸🇦 العربية", web_app=WebAppInfo(url=f"{WEBAPP_URL}?lang=ar")),
        ],
        [
            InlineKeyboardButton("🦷 Braces (Charter's - አማርኛ)", web_app=WebAppInfo(url=f"{WEBAPP_URL}?method=charters&lang=am")),
        ],
    ]

    reply_kb = ReplyKeyboardMarkup(
        [[KeyboardButton("⏱️ Start 2-Min Brushing Session", web_app=WebAppInfo(url=WEBAPP_URL))]],
        resize_keyboard=True,
    )

    await update.message.reply_text(
        "🦷 *DentGuide Pro — Clinical Oral Hygiene Suite*\n\n"
        "Choose your language below to open the interactive brushing animations and 2-minute quadrant pacer:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    await update.message.reply_text(
        "Tap the bottom button anytime to log your daily brushing streak, or send /remind for 7:00 AM & 9:30 PM alerts:",
        reply_markup=reply_kb,
    )


async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = update.effective_message.web_app_data.data
    data = json.loads(raw)
    chat_id = update.effective_chat.id
    user = update.effective_user

    if data.get("event") == "brushing_completed":
        streak = data.get("streak", 1)
        lang = data.get("lang", "en")
        method = data.get("method", "modified_bass").replace("_", " ").title()

        upsert_user(chat_id, user.first_name if user else "", streak=streak, lang=lang)

        await update.message.reply_text(
            f"✅ *2-Minute Session Complete!*\n"
            f"• Technique Practiced: *{method}*\n"
            f"• Cloud Streak Saved: 🔥 *{streak} sessions*\n\n"
            f"Tip: Spit out excess foam, but avoid rinsing heavily with water right away so fluoride keeps protecting your enamel!",
            parse_mode="Markdown",
        )


async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = update.effective_user
    schedule_user_reminders(context.job_queue, chat_id)
    upsert_user(chat_id, user.first_name if user else "", remind_enabled=True)

    await update.message.reply_text(
        "🔔 *Automated Daily Reminders Activated!*\n"
        "You will receive a brushing prompt at *7:00 AM* and *9:30 PM* (East Africa Time). Send /stopremind anytime to pause.",
        parse_mode="Markdown",
    )


async def stop_remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    for job in context.job_queue.get_jobs_by_name(str(chat_id)):
        job.schedule_removal()
    upsert_user(chat_id, remind_enabled=False)
    await update.message.reply_text("🔕 Daily brushing reminders paused.")


async def send_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🪥 Start 2-Min Brushing Timer", web_app=WebAppInfo(url=WEBAPP_URL))
    ]])
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text="🦷 *Time for your 2-minute brushing session!* Angle at 45° toward the gumline:",
        parse_mode="Markdown",
        reply_markup=kb,
    )


def main() -> None:
    # Start background HTTP server for cloud keep-alive
    threading.Thread(target=start_health_server, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).post_init(restore_reminders_on_boot).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("remind", remind_command))
    app.add_handler(CommandHandler("stopremind", stop_remind_command))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data))

    print("✅ DentGuide Cloud Bot is live 24/7...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()