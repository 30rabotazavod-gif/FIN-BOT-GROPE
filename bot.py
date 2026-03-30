import logging
import re
import os
from datetime import datetime
from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    filters,
    ContextTypes,
)
from database import init_db, add_transaction, get_balance, get_recent_transactions

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# CONFIG — читается из переменных окружения
# ─────────────────────────────────────────────
BOT_TOKEN    = os.environ["BOT_TOKEN"]
ADMIN_ID     = int(os.environ["ADMIN_ID"])          # Telegram user_id главного админа
ALLOWED_GROUP = int(os.environ["ALLOWED_GROUP_ID"]) # Telegram chat_id группы (отрицательное число)

# Дополнительные пользователи, которым разрешён /balance в ЛС
# Через запятую: "123456,789012"
ALLOWED_USERS_RAW = os.environ.get("ALLOWED_USER_IDS", "")
ALLOWED_USERS = set()
if ALLOWED_USERS_RAW.strip():
    ALLOWED_USERS = {int(uid.strip()) for uid in ALLOWED_USERS_RAW.split(",") if uid.strip()}
ALLOWED_USERS.add(ADMIN_ID)  # Админ всегда разрешён


# ─────────────────────────────────────────────
# ПАРСЕР ТРАНЗАКЦИЙ
# ─────────────────────────────────────────────
def parse_transaction(text: str):
    """
    Возвращает dict или None.
    Формат сообщения:
        [+/-] <сумма> [$] [комментарий]
    Примеры:
        +600$ 12200
        +28.297.000 QAHRAMON BEKABOD
        -150000 аренда
        50000
    """
    text = text.strip()

    # Определяем тип: доход / расход
    if text.startswith("+"):
        t_type = "income"
        text = text[1:].strip()
    elif text.startswith("-"):
        t_type = "expense"
        text = text[1:].strip()
    else:
        t_type = "expense"   # без знака → расход

    # Ищем число (с возможными точками-разделителями тысяч)
    match = re.search(r"[\d][\d.]*", text)
    if not match:
        return None

    raw_number = match.group().replace(".", "")
    try:
        amount = int(raw_number)
    except ValueError:
        return None

    if amount <= 0:
        return None

    # Определяем валюту
    currency = "USD" if "$" in text else "UZS"

    # Комментарий — всё после числа (и после $, если есть)
    rest = text[match.end():].replace("$", "").strip()
    comment = rest if rest else ""

    return {
        "type": t_type,
        "amount": amount,
        "currency": currency,
        "comment": comment,
    }


def format_amount(amount: int, currency: str) -> str:
    if currency == "UZS":
        return f"{amount:,} UZS".replace(",", " ")
    return f"{amount:,} $".replace(",", " ")


# ─────────────────────────────────────────────
# ХЭНДЛЕРЫ
# ─────────────────────────────────────────────
async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Слушаем сообщения в разрешённой группе, НЕ отвечаем в группе."""
    if not update.message or not update.message.text:
        return

    msg   = update.message
    chat  = msg.chat
    user  = msg.from_user
    text  = msg.text.strip()

    # Безопасность: только наша группа
    if chat.id != ALLOWED_GROUP:
        return

    tx = parse_transaction(text)

    user_display = f"@{user.username}" if user.username else user.full_name

    if tx is None:
        # Уведомляем только если в тексте нет вообще цифр — иначе много шума
        if not re.search(r"\d", text):
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    f"⚠️ <b>Некорректная запись</b>\n"
                    f"👤 {user_display}\n"
                    f"📝 <code>{text}</code>"
                ),
                parse_mode="HTML",
            )
        return

    # Сохраняем в БД
    sign = 1 if tx["type"] == "income" else -1
    add_transaction(
        user_id=user.id,
        username=user_display,
        amount=sign * tx["amount"],
        currency=tx["currency"],
        comment=tx["comment"],
        raw_text=text,
    )

    # Уведомление админу
    icon = "📥" if tx["type"] == "income" else "📤"
    sign_str = "+" if tx["type"] == "income" else "-"
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            f"{icon} <b>Запись принята</b>\n"
            f"👤 {user_display}\n"
            f"💰 {sign_str}{format_amount(tx['amount'], tx['currency'])}\n"
            f"📝 {tx['comment'] or '—'}"
        ),
        parse_mode="HTML",
    )


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /balance или /start в ЛС."""
    user = update.effective_user
    if user.id not in ALLOWED_USERS:
        await update.message.reply_text("⛔ У вас нет доступа.")
        return

    bal = get_balance()
    recent = get_recent_transactions(5)

    uzs = bal.get("UZS", 0)
    usd = bal.get("USD", 0)

    lines = [
        "💰 <b>Текущий баланс</b>",
        "",
        f"💵 <b>Доллары:</b>  {format_amount(abs(usd), 'USD')} {'📈' if usd >= 0 else '📉'}",
        f"💳 <b>Сумы:</b>  {format_amount(abs(uzs), 'UZS')} {'📈' if uzs >= 0 else '📉'}",
    ]

    if recent:
        lines += ["", "─────────────────────", "🕐 <b>Последние 5 операций:</b>"]
        for r in recent:
            sign = "+" if r["amount"] > 0 else ""
            lines.append(
                f"  {sign}{format_amount(abs(r['amount']), r['currency'])} "
                f"| {r['username']} | {r['comment'] or '—'}"
            )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await balance_command(update, context)


# ─────────────────────────────────────────────
# ЗАПУСК
# ─────────────────────────────────────────────
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # Команды в личных сообщениях
    app.add_handler(CommandHandler("start",   start_command,   filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("balance", balance_command, filters=filters.ChatType.PRIVATE))

    # Все текстовые сообщения из группы
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.ChatType.GROUPS,
            handle_group_message,
        )
    )

    logger.info("Bot started.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
