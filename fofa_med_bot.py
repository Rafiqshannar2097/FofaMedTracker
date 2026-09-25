import os
import sqlite3
import datetime
import pytz
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)
from google import genai

# ==========================================
# ⚙️ الإعدادات والمفاتيح الأساسية
# ==========================================
TELEGRAM_TOKEN = "8408474332:AAEj4SrZUd4621fF2MoOhqaCdYPgJGM7JXo"
GEMINI_API_KEY = "AQ.Ab8RN6KOOAYSYeSWHNiQ0coeB6klpWeUsrKRJPOcefN7GhBblw"
SUPERVISOR_CHAT_ID = 1454870918

DB_NAME = "medications.db"
# تعديل المنطقة الزمنية لتكون بتوقيت الأردن (عمان / الزرقاء)
TIMEZONE = pytz.timezone("Asia/Amman")

# إعداد عميل Gemini للذكاء الاصطناعي
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# ==========================================
# 🗄️ التعامل مع قاعدة البيانات (SQLite)
# ==========================================
def init_db():
    """إنشاء جدول الأدوية إذا لم يكن موجوداً"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS medications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            medicine_name TEXT NOT NULL,
            reminder_time TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def add_predefined_meds(patient_chat_id: int):
    """إضافة قائمة الأدوية المحددة مسبقاً للمريض فور الضغط على Start"""
    default_meds = [
        ("Lansazol", "12:00"),
        ("Imuran + B Complex", "12:30"),
        ("Fe", "14:30"),
        ("Fe + Folic Acid", "18:30"),
        ("Fe", "21:30"),
        ("Imuran", "22:30"),
        ("Zinc", "23:30")
    ]
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    for med_name, time_str in default_meds:
        cursor.execute(
            "SELECT id FROM medications WHERE chat_id = ? AND medicine_name = ? AND reminder_time = ?",
            (patient_chat_id, med_name, time_str)
        )
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO medications (chat_id, medicine_name, reminder_time) VALUES (?, ?, ?)",
                (patient_chat_id, med_name, time_str)
            )
    
    conn.commit()
    conn.close()

def get_all_medications():
    """جلب جميع الأدوية المسجلة لإعادة جدولتها عند تشغيل البوت"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, chat_id, medicine_name, reminder_time FROM medications")
    rows = cursor.fetchall()
    conn.close()
    return rows

# ==========================================
# ⏰ نظام التذكير والجدولة المتكررة
# ==========================================
async def repeat_reminder_task(context: ContextTypes.DEFAULT_TYPE):
    """دالة تتكرر كل 30 ثانية لتنبيه المريض"""
    job_data = context.job.data
    chat_id = job_data["chat_id"]
    med_name = job_data["medicine_name"]
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"⚠️ فوفاااا عالسريععع صار وقت الدوا!\n\n"
             f"يلا فوفا عندك هاد الدوا : {med_name}.\n\n"
             f"📸 ما رح يوقف التذكير لحتى تصوري حبة الدوا وتبعتيها لهون هلق, يلا يا قلبي!"
    )

async def trigger_daily_reminder(context: ContextTypes.DEFAULT_TYPE):
    """تُستدعى في الوقت المحدد يومياً لبدء التذكير المتكرر"""
    job_data = context.job.data
    chat_id = job_data["chat_id"]
    med_name = job_data["medicine_name"]

    # تفعيل حالة انتظار الصورة
    context.application.user_data[chat_id] = {
        "waiting_for_photo": True,
        "active_medicine": med_name
    }

    # إلغاء أي تنبيهات نشطة قديمة لنفس المريض لمنع التداخل
    active_jobs = context.job_queue.get_jobs_by_name(f"active_alert_{chat_id}")
    for job in active_jobs:
        job.schedule_removal()

    # بدء التذكير المتكرر كل 30 ثانية (interval=30)
    context.job_queue.run_repeating(
        repeat_reminder_task,
        interval=30,
        first=0,
        data={"chat_id": chat_id, "medicine_name": med_name},
        name=f"active_alert_{chat_id}"
    )

def schedule_med_job(job_queue, chat_id: int, med_name: str, reminder_time_str: str):
    """جدولة موعد يومي مع ربط التوقيت بمنطقة الأردن لتفادي اختلاف توقيت السيرفر"""
    job_name = f"daily_{chat_id}_{med_name}_{reminder_time_str}"
    
    # تفادي إضافة الوظيفة ذاتها إذا كانت مجدولة مسبقاً
    if job_queue.get_jobs_by_name(job_name):
        return

    # إسناد التوقيت المحلي للأردن بشكل صريح لـ time_obj
    naive_time = datetime.datetime.strptime(reminder_time_str, "%H:%M").time()
    time_with_tz = naive_time.replace(tzinfo=TIMEZONE)
    
    job_queue.run_daily(
        trigger_daily_reminder,
        time=time_with_tz,
        days=(0, 1, 2, 3, 4, 5, 6),
        data={"chat_id": chat_id, "medicine_name": med_name},
        name=job_name
    )

# ==========================================
# 📷 فحص الصورة بالذكاء الاصطناعي والإشعارات
# ==========================================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_state = context.application.user_data.get(chat_id, {})

    if not user_state.get("waiting_for_photo"):
        await update.message.reply_text("شكراً لك! لكن لا يوجد تذكير دواء نشط بانتظار صورة حالياً.")
        return

    med_name = user_state.get("active_medicine", "الدواء")
    user_name = update.effective_user.first_name or "المريض"

    await update.message.reply_text("⏳ استني شوي بس عم اتحقق من الدواء...")

    photo = update.message.photo[-1]
    photo_file = await photo.get_file()
    file_path = f"temp_{chat_id}.jpg"
    await photo_file.download_to_drive(file_path)

    try:
        with open(file_path, "rb") as f:
            image_bytes = f.read()

        prompt = (
            "هل تحتوي هذه الصورة على حبة دواء أو علبة دواء أو شريط أدوية؟ "
            "أجب بـ YES فقط إذا كانت تحتوي دواء بوضوح، أو NO إذا كانت لا تحتوي دواء."
        )

        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[
                {"mime_type": "image/jpeg", "data": image_bytes},
                prompt
            ]
        )

        result = response.text.strip().upper()

        if "YES" in result:
            # 1. إيقاف التذكير المتكرر
            active_jobs = context.job_queue.get_jobs_by_name(f"active_alert_{chat_id}")
            for job in active_jobs:
                job.schedule_removal()

            # 2. إغلاق حالة الانتظار
            context.application.user_data[chat_id]["waiting_for_photo"] = False

            # 3. إبلاغ المريض
            await update.message.reply_text("🩷 مشي الحال فوفا! تأكدت من صورة الدوا ووقفت المنبه. بالعافية يا روحي!")

            # 4. إرسال إشعار ونسخة من الصورة للمراقب
            if SUPERVISOR_CHAT_ID:
                try:
                    current_time_str = datetime.datetime.now(TIMEZONE).strftime('%H:%M')
                    await context.bot.send_photo(
                        chat_id=SUPERVISOR_CHAT_ID,
                        photo=photo.file_id,
                        caption=f"🔔 **إشعار مراقبة الأدوية:**\n\n"
                                f"قام المريض **{user_name}** بتناول دواء: **{med_name}** "
                                f"في الساعة {current_time_str} (توقيت الأردن).\n"
                                f"تم التأكد من صورة الحبة بنجاح."
                    )
                except Exception as e:
                    print(f"فشل إرسال الإشعار للمراقب: {e}")

        else:
            await update.message.reply_text("❌ ما قدرت اتعرف على الدوا بهي الصورة. لا تزوغلي. صوري الدوا منيح لوقف التذكير!")

    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

# ==========================================
# 💬 أوامر البوت
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    # إضافة المواعيد المجهزة تلقائياً
    add_predefined_meds(chat_id)
    
    # جدولة المواعيد الخاصة بهذا المريض
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT medicine_name, reminder_time FROM medications WHERE chat_id = ?", (chat_id,))
    user_meds = cursor.fetchall()
    conn.close()

    for med_name, reminder_time in user_meds:
        schedule_med_job(context.job_queue, chat_id, med_name, reminder_time)
    
    await update.message.reply_text(
        "كيفك فوفا! اًنا هون لساعدك تاخدي ادويتك بمواعيدها. 💊\n\n"
        "رح ذكرك بالمواعيد المحددة ، وما رح يوقف التذكير حتى ترسلي صورة الحبة لكل موعد.\n\n"
        "اكبسي هون  `/my_meds` لتشوفي قائمة أوديتك ومواعيدها."
    )

async def list_meds_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT medicine_name, reminder_time FROM medications WHERE chat_id = ? ORDER BY reminder_time ASC", (chat_id,))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("لا يوجد أدوية مسجلة حالياً.")
        return

    msg = "📋هي أوديتك يا روحي :\n\n"
    for name, time_val in rows:
        msg += f"• {name} ⬅️ الساعة: {time_val}\n"
    
    await update.message.reply_text(msg)

def restore_scheduled_jobs(job_queue):
    """إعادة الجدولة عند تشغيل الخادم"""
    meds = get_all_medications()
    for _, chat_id, med_name, reminder_time in meds:
        schedule_med_job(job_queue, chat_id, med_name, reminder_time)

# ==========================================
# 🚀 بدء التشغيل
# ==========================================
if __name__ == '__main__':
    init_db()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    if app.job_queue:
        app.job_queue.scheduler.timezone = TIMEZONE

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("my_meds", list_meds_command))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    restore_scheduled_jobs(app.job_queue)

    print("البوت يعمل ومستعد لتذكير المريض...")
    app.run_polling()