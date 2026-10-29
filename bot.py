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

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
WEBAPP_URL = os.environ.get(
    "WEBAPP_URL",
    "https://asefayirgu-hue.github.io/dentguide-pro/index.html",
).strip()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()

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
        return


def start_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


# --- 2. Clinical Rank & Supabase Persistence Helpers ---
def get_clinical_rank(streak: int) -> str:
    if streak >= 30:
        return "👑 Periodontal Master"
    if streak >= 14:
        return "💎 7-Day Plaque-Free Pro"
    if streak >= 7:
        return "🥇 Enamel Guardian"
    if streak >= 3:
        return "🥈 Sulcus Defender"
    return "🌱 Hygiene Starter"


def upsert_user(chat_id: int, first_name: str = "", **fields):
    if not supabase:
        return
    payload = {"chat_id": chat_id, "first_name": first_name, **fields}
    try:
        supabase.table("dentguide_users").upsert(payload).execute()
    except Exception as e:
        print(f"Supabase upsert warning: {e}")


def get_user_record(chat_id: int) -> dict:
    if not supabase:
        return {"streak": 0, "last_method": "modified_bass", "remind_enabled": False}
    try:
        res = supabase.table("dentguide_users").select("*").eq("chat_id", chat_id).execute()
        if res.data:
            return res.data[0]
    except Exception as e:
        print(f"Supabase fetch warning: {e}")
    return {"streak": 0, "last_method": "modified_bass", "remind_enabled": False}


def schedule_user_reminders(job_queue, chat_id: int):
    if job_queue is None:
        return
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
    if not supabase or app.job_queue is None:
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
            InlineKeyboardButton("🧵 Interdental C-Floss", web_app=WebAppInfo(url=f"{WEBAPP_URL}?method=modified_bass&view=interdental&lang=am")),
        ],
        [
            InlineKeyboardButton("💧 Paste Dosage (3D)", web_app=WebAppInfo(url=f"{WEBAPP_URL}?view=paste&lang=am")),
            InlineKeyboardButton("👅 Tongue Cleaning", web_app=WebAppInfo(url=f"{WEBAPP_URL}?view=tongue&lang=am")),
        ],
    ]

    reply_kb = ReplyKeyboardMarkup(
        [[KeyboardButton("⏱️ Start 2-Min Brushing Session", web_app=WebAppInfo(url=WEBAPP_URL))]],
        resize_keyboard=True,
    )

    await update.message.reply_text(
        "🦷 *DentAlign Pro — Clinical Oral Hygiene Suite*\n\n"
        "Select your language below to open the 60fps brushing biomechanics stage and 2-minute quadrant pacer.\n\n"
        "📌 *Quick Commands:*\n"
        "• `/stats` — View your brushing streak & rank\n"
        "• `/leaderboard` — Top 10 clinic streak champions\n"
        "• `/postop` — Braces, gum recession & flossing care\n"
        "• `/remind` — Enable 7:00 AM & 9:30 PM alerts",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    await update.message.reply_text(
        "Tap the bottom button anytime to log your daily brushing streak:",
        reply_markup=reply_kb,
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows the patient's personal cloud brushing statistics and clinical rank."""
    chat_id = update.effective_chat.id
    rec = get_user_record(chat_id)
    streak = rec.get("streak") or 0
    method = (rec.get("last_method") or "modified_bass").replace("_", " ").title()
    remind_on = "✅ Active (7:00 AM & 9:30 PM)" if rec.get("remind_enabled") else "🔕 Off (Send /remind)"
    rank = get_clinical_rank(streak)
    next_badge = ((streak // 14) + 1) * 14

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🪥 Practice 2-Min Session Now", web_app=WebAppInfo(url=WEBAPP_URL))
    ]])

    await update.message.reply_text(
        f"📊 *Your Clinical Oral Hygiene Profile*\n\n"
        f"• *Current Rank:* {rank}\n"
        f"• *Completed 2-Min Sessions:* 🔥 *{streak}*\n"
        f"• *Last Practiced Technique:* *{method}*\n"
        f"• *Daily Reminders:* {remind_on}\n"
        f"• *Next 7-Day Milestone Certificate:* {next_badge - streak} sessions away ({next_badge} total)",
        parse_mode="Markdown",
        reply_markup=kb,
    )


async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the top 10 brushing streak patients from Supabase."""
    if not supabase:
        await update.message.reply_text("Cloud leaderboard requires SUPABASE_URL and SUPABASE_KEY to be configured.")
        return

    try:
        res = (
            supabase.table("dentguide_users")
            .select("first_name, streak")
            .order("streak", desc=True)
            .limit(10)
            .execute()
        )
        rows = res.data or []
        if not rows:
            await update.message.reply_text("No completed sessions logged yet. Be the first by finishing a 2-minute session!")
            return

        medals = ["🥇", "🥈", "🥉"]
        lines = ["🏆 *DentAlign Pro — Top 10 Streak Leaderboard*\n"]
        for idx, r in enumerate(rows):
            icon = medals[idx] if idx < 3 else f"`#{idx + 1}`"
            name = r.get("first_name") or "Patient"
            st = r.get("streak") or 0
            lines.append(f"{icon} *{name}* — 🔥 {st} sessions ({get_clinical_rank(st)})")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Could not load leaderboard: {e}")


async def postop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Interactive post-op & specialized care deep-link menu."""
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🦷 Braces & Bridges (Charter's 45°)", web_app=WebAppInfo(url=f"{WEBAPP_URL}?method=charters&lang=am"))],
        [InlineKeyboardButton("🛡️ Gum Recession & Sensitivity (Stillman's)", web_app=WebAppInfo(url=f"{WEBAPP_URL}?method=modified_stillman&lang=am"))],
        [InlineKeyboardButton("⚡ Lingual Surface & Enamel Wear Care", web_app=WebAppInfo(url=f"{WEBAPP_URL}?view=lingual&lang=am"))],
        [InlineKeyboardButton("🦷 Occlusal Molar Fissure Brushing", web_app=WebAppInfo(url=f"{WEBAPP_URL}?view=occlusal&lang=am"))],
        [InlineKeyboardButton("💧 Fluoride Toothpaste Dosage Guide", web_app=WebAppInfo(url=f"{WEBAPP_URL}?view=paste&lang=am"))],
        [InlineKeyboardButton("👅 Tongue Scraper & Dorsum Cleaning", web_app=WebAppInfo(url=f"{WEBAPP_URL}?view=tongue&lang=am"))],
        [InlineKeyboardButton("🧵 Interdental C-Floss & Superfloss", web_app=WebAppInfo(url=f"{WEBAPP_URL}?method=modified_bass&view=interdental&lang=am"))],
        [InlineKeyboardButton("⚠️ Why Horizontal Scrubbing Erodes Teeth", web_app=WebAppInfo(url=f"{WEBAPP_URL}?method=horizontal_scrub&lang=am"))],
    ])
    await update.message.reply_text(
        "🩺 *Specialized Clinical & Post-Op Prescriptions*\n\n"
        "Select your clinical condition below to open the exact biomechanical demonstration:",
        parse_mode="Markdown",
        reply_markup=kb,
    )


async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = update.effective_message.web_app_data.data
    data = json.loads(raw)
    chat_id = update.effective_chat.id
    user = update.effective_user

    if data.get("event") == "brushing_completed":
        streak = int(data.get("streak", 1))
        lang = data.get("lang", "en")
        raw_method = data.get("method", "modified_bass")
        method = raw_method.replace("_", " ").title()
        rank = get_clinical_rank(streak)

        upsert_user(
            chat_id,
            user.first_name if user else "",
            streak=streak,
            lang=lang,
            last_method=raw_method,
        )

        await update.message.reply_text(
            f"✅ *2-Minute Session Complete!*\n"
            f"• Technique Practiced: *{method}*\n"
            f"• Cloud Streak Saved: 🔥 *{streak} sessions*\n"
            f"• Clinical Rank: *{rank}*\n\n"
            f"Tip: Spit out excess foam, but avoid rinsing heavily with water right away so fluoride keeps protecting your enamel!",
            parse_mode="Markdown",
        )

        # Automatic 7-Day Milestone Certificate (every 14 sessions = 7 days morning + night)
        if streak > 0 and streak % 14 == 0:
            weeks = streak // 14
            await update.message.reply_text(
                f"🎉🏆 *CONGRATULATIONS! {weeks}-WEEK PLAQUE-FREE MILESTONE!* 🏆🎉\n\n"
                f"You have completed *{streak} guided 2-minute brushing sessions*!\n"
                f"Show this certificate badge at your next dental checkup. Keep protecting your gingival health!",
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
    if context.job_queue is not None:
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
    if not BOT_TOKEN:
        raise RuntimeError("Missing BOT_TOKEN environment variable. Set BOT_TOKEN in Render or your .env file.")

    threading.Thread(target=start_health_server, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).post_init(restore_reminders_on_boot).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("leaderboard", leaderboard_command))
    app.add_handler(CommandHandler("postop", postop_command))
    app.add_handler(CommandHandler("remind", remind_command))
    app.add_handler(CommandHandler("stopremind", stop_remind_command))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data))

    print("✅ DentGuide Cloud Bot is live 24/7 with /stats, /leaderboard, and /postop...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
