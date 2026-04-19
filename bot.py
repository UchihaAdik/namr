import os
import json
import logging
import re
from datetime import datetime
import httpx
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")

GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"

SYSTEM_PROMPT = """Ты умный личный ассистент. Пользователь пишет одним сообщением несколько событий сразу.

Разбери ВСЕ события и верни JSON-массив в теге <records>.

Формат каждой записи:
{
  "category": "sport|task|finance|note|other",
  "summary": "краткое название 3-5 слов",
  "details": "детали одной строкой",
  "emoji": "одно эмодзи",
  "deadline": "дата если упомянута или null",
  "amount": число если есть сумма денег или null
}

Категории:
- sport = спорт, тренировка, отжимания, подтягивания, бег, зал
- task = задача, дедлайн, сделать, доделать, напомнить
- finance = потратил, купил, стоит, сом, руб, баксов, долларов, обед, ужин, транспорт, свидание
- note = заметка, идея, мысль
- other = всё остальное

Правила:
- Создавай ОТДЕЛЬНУЮ запись для каждого события
- summary — максимум 5 слов
- Отвечай ТОЛЬКО: одно предложение подтверждения + тег <records>[...]</records>"""

CAT_LABELS = {
    "sport": "🏃 Спорт",
    "task": "✅ Задача",
    "finance": "💰 Финансы",
    "note": "📝 Заметка",
    "other": "📌 Другое"
}

CAT_SHEETS = {
    "sport": "Спорт",
    "task": "Задачи",
    "finance": "Финансы",
    "note": "Заметки",
    "other": "Другое"
}

def get_sheets_client():
    creds_data = json.loads(GOOGLE_CREDS_JSON)
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(creds_data, scopes=scopes)
    return gspread.authorize(creds)

def ensure_headers(sheet):
    if not sheet.row_values(1):
        sheet.append_row(["Дата", "Время", "Категория", "Событие", "Детали", "Сумма", "Дедлайн"])

def save_records(records):
    try:
        client = get_sheets_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        now = datetime.now()
        for rec in records:
            cat = rec.get("category", "other")
            sheet_name = CAT_SHEETS.get(cat, "Другое")
            try:
                sheet = spreadsheet.worksheet(sheet_name)
            except gspread.WorksheetNotFound:
                sheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=10)
            ensure_headers(sheet)
            sheet.append_row([
                now.strftime("%d.%m.%Y"),
                now.strftime("%H:%M"),
                sheet_name,
                rec.get("summary", ""),
                rec.get("details", ""),
                rec.get("amount") or "",
                rec.get("deadline") or ""
            ])
        return True
    except Exception as e:
        logger.error(f"Sheets error: {e}")
        return False

async def parse_with_gemini(text: str):
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            res = await client.post(GEMINI_URL, json={
                "contents": [{"parts": [{"text": SYSTEM_PROMPT + "\n\nСообщение: " + text}]}]
            })
            data = res.json()
            full = data["candidates"][0]["content"]["parts"][0]["text"]
            match = re.search(r"<records>([\s\S]*?)</records>", full)
            reply = re.sub(r"<records>[\s\S]*?</records>", "", full).strip()
            records = []
            if match:
                try:
                    records = json.loads(match.group(1).strip())
                except:
                    pass
            return reply, records
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        return None, []

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я твой личный ассистент.\n\n"
        "Пиши мне что угодно одним сообщением:\n"
        "• Траты: «обед 150, такси 200»\n"
        "• Спорт: «отжался 20 раз»\n"
        "• Задачи: «завтра сдать отчёт»\n"
        "• Всё вместе в одном сообщении!\n\n"
        "Всё автоматически сохраняется в Google Sheets 📊"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text:
        return

    await update.message.reply_text("⏳ Обрабатываю...")

    reply, records = await parse_with_gemini(text)

    if not records:
        await update.message.reply_text("Не смог разобрать 😕 Попробуй иначе")
        return

    saved = save_records(records)

    lines = [reply or f"Записал {len(records)} событий!", ""]
    for r in records:
        cat_label = CAT_LABELS.get(r.get("category", "other"), "📌 Другое")
        line = f"{r.get('emoji', '')} {cat_label} — {r.get('summary', '')}"
        if r.get("details"):
            line += f"\n   {r['details']}"
        if r.get("amount"):
            line += f"\n   💵 {r['amount']}"
        if r.get("deadline"):
            line += f"\n   📅 до: {r['deadline']}"
        lines.append(line)

    lines.append("")
    lines.append("✅ Сохранено в таблицу" if saved else "⚠️ Ошибка сохранения в таблицу")

    await update.message.reply_text("\n".join(lines))

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot started!")
    app.run_polling()

if __name__ == "__main__":
    main()
