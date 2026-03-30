import logging
import re
import os
from datetime import datetime, date, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
from database import (
    init_db,
    add_transaction,
    delete_transaction,
    edit_transaction_comment,
    get_transaction_by_id,
    get_balance,
    get_recent_transactions,
    get_report,
    get_start_date,
    set_start_date,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
BOT_TOKEN     = os.environ["BOT_TOKEN"]
ADMIN_ID      = int(os.environ["ADMIN_ID"])
ALLOWED_GROUP = int(os.environ["ALLOWED_GROUP_ID"])

ALLOWED_USERS_RAW = os.environ.get("ALLOWED_USER_IDS", "")
ALLOWED_USERS = set()
if ALLOWED_USERS_RAW.strip():
    ALLOWED_USERS = {int(uid.strip()) for uid in ALLOWED_USERS_RAW.split(",") if uid.strip()}
ALLOWED_USERS.add(ADMIN_ID)

# ConversationHandler states
WAITING_DELETE_ID   = 1
WAITING_EDIT_ID     = 2
WAITING_EDIT_TEXT   = 3
WAITING_SETSTART    = 4

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def fmt(amount: int, currency: str) -> str:
    if currency == "UZS":
        return f"{abs(amount):,} UZS".replace(",", " ")
    return f"{abs(amount):,} $".replace(",", " ")


def parse_transaction(text: str):
    text = text.strip()
    if text.startswith("+"):
        t_type = "income"
        text = text[1:].strip()
    elif text.startswith("-"):
        t_type = "expense"
        text = text[1:].strip()
    else:
        t_type = "expense"

    match = re.search(r"\d[\d.]*", text)
    if not match:
        return None

    raw_number = match.group().replace(".", "")
    try:
        amount = int(raw_number)
    except ValueError:
        return None

    if amount <= 0:
        return None

    currency = "USD" if "$" in text else "UZS"
    rest = text[match.end():].replace("$", "").strip()
    return {"type": t_type, "amount": amount, "currency": currency, "comment": rest}


def parse_date(s: str):
    for fmt_str in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(s.strip(), fmt_str).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def is_allowed(user_id: int) -> bool:
    return user_id in ALLOWED_USERS


# ─────────────────────────────────────────────
# КЛАВИАТУРЫ
# ─────────────────────────────────────────────
def main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Главное меню — для всех разрешённых пользователей."""
    rows = [
        [
            InlineKeyboardButton("💰 Баланс",        callback_data="menu:balance"),
            InlineKeyboardButton("📊 Отчёты",        callback_data="menu:reports"),
        ],
    ]
    if is_admin(user_id):
        rows.append([
            InlineKeyboardButton("🔧 Управление",    callback_data="menu:admin"),
            InlineKeyboardButton("📅 Дата начала",   callback_data="menu:setstart"),
        ])
    rows.append([
        InlineKeyboardButton("❓ Помощь",            callback_data="menu:help"),
    ])
    return InlineKeyboardMarkup(rows)


def reports_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📅 Сегодня",       callback_data="report:today"),
            InlineKeyboardButton("📆 Неделя",        callback_data="report:week"),
        ],
        [
            InlineKeyboardButton("🗓 Месяц",         callback_data="report:month"),
            InlineKeyboardButton("✏️ Период...",     callback_data="report:custom"),
        ],
        [InlineKeyboardButton("◀️ Назад",            callback_data="menu:main")],
    ])


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🗑 Удалить запись", callback_data="admin:delete"),
            InlineKeyboardButton("✏️ Изменить коммент", callback_data="admin:edit"),
        ],
        [
            InlineKeyboardButton("🕐 Последние 10",  callback_data="admin:recent"),
        ],
        [InlineKeyboardButton("◀️ Назад",            callback_data="menu:main")],
    ])


def back_keyboard(target="menu:main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад в меню", callback_data=target)]])


# ─────────────────────────────────────────────
# ТЕКСТЫ
# ─────────────────────────────────────────────
async def send_balance_text(bot, chat_id):
    bal    = get_balance()
    recent = get_recent_transactions(5)
    uzs    = bal.get("UZS", 0)
    usd    = bal.get("USD", 0)
    start  = get_start_date()

    start_line = f"\n📅 Учёт с: <b>{start}</b>" if start else ""
    lines = [
        f"💰 <b>Текущий баланс</b>{start_line}",
        "",
        f"💵 <b>USD:</b>  {'📈' if usd >= 0 else '📉'} {fmt(usd, 'USD')}",
        f"💳 <b>UZS:</b>  {'📈' if uzs >= 0 else '📉'} {fmt(uzs, 'UZS')}",
    ]
    if recent:
        lines += ["", "─────────────────", "🕐 <b>Последние 5 операций:</b>"]
        for r in recent:
            sign = "+" if r["amount"] > 0 else ""
            lines.append(
                f"  <b>#{r['id']}</b> {sign}{fmt(r['amount'], r['currency'])}"
                f" | {r['username']} | {r['comment'] or '—'}"
            )
    return "\n".join(lines)


def build_report_text(from_date, to_date, label):
    r = get_report(from_date, to_date)

    def sign_icon(val):
        return "📈" if val >= 0 else "📉"

    text = (
        f"📊 <b>Отчёт: {label}</b>\n"
        f"📅 {from_date} → {to_date}\n"
        f"📝 Записей: {r['count']}\n\n"
        f"━━━━━━  💵 USD  ━━━━━━\n"
        f"📥 Доход:   +{fmt(r['income_usd'],  'USD')}\n"
        f"📤 Расход:  -{fmt(r['expense_usd'], 'USD')}\n"
        f"{sign_icon(r['balance_usd'])} Итого:   {'+' if r['balance_usd'] >= 0 else ''}{fmt(r['balance_usd'], 'USD')}\n\n"
        f"━━━━━━  💳 UZS  ━━━━━━\n"
        f"📥 Доход:   +{fmt(r['income_uzs'],  'UZS')}\n"
        f"📤 Расход:  -{fmt(r['expense_uzs'], 'UZS')}\n"
        f"{sign_icon(r['balance_uzs'])} Итого:   {'+' if r['balance_uzs'] >= 0 else ''}{fmt(r['balance_uzs'], 'UZS')}\n"
    )
    txs = r["transactions"][-10:]
    if txs:
        text += "\n─────────────────────\n<b>Записи периода:</b>\n"
        for t in reversed(txs):
            sign = "+" if t["amount"] > 0 else ""
            dt   = t["created_at"][5:10]
            text += f"  <b>#{t['id']}</b> {dt} {sign}{fmt(t['amount'], t['currency'])} | {t['comment'] or '—'}\n"
    return text


# ─────────────────────────────────────────────
# ГРУППА: парсинг транзакций
# ─────────────────────────────────────────────
async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    msg  = update.message
    user = msg.from_user
    text = msg.text.strip()

    if msg.chat.id != ALLOWED_GROUP:
        return

    tx = parse_transaction(text)
    user_display = f"@{user.username}" if user.username else user.full_name

    if tx is None:
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

    sign  = 1 if tx["type"] == "income" else -1
    tx_id = add_transaction(
        user_id=user.id,
        username=user_display,
        amount=sign * tx["amount"],
        currency=tx["currency"],
        comment=tx["comment"],
        raw_text=text,
    )

    if tx_id == -1:
        start = get_start_date()
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"⏭ <b>Запись проигнорирована</b> (до даты начала {start})\n"
                f"👤 {user_display}: <code>{text}</code>"
            ),
            parse_mode="HTML",
        )
        return

    icon     = "📥" if tx["type"] == "income" else "📤"
    sign_str = "+" if tx["type"] == "income" else "-"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🗑 Удалить #{tx_id}", callback_data=f"del:{tx_id}")]
    ])

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            f"{icon} <b>Запись #{tx_id} принята</b>\n"
            f"👤 {user_display}\n"
            f"💰 {sign_str}{fmt(tx['amount'], tx['currency'])}\n"
            f"📝 {tx['comment'] or '—'}"
        ),
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ─────────────────────────────────────────────
# /start — главное меню
# ─────────────────────────────────────────────
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_allowed(user.id):
        await update.message.reply_text("⛔ У вас нет доступа.")
        return

    greeting = "👋 Привет, <b>Администратор</b>!" if is_admin(user.id) else f"👋 Привет, <b>{user.first_name}</b>!"
    await update.message.reply_text(
        f"{greeting}\n\nВыберите действие:",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(user.id),
    )


# ─────────────────────────────────────────────
# ГЛАВНЫЙ ОБРАБОТЧИК КНОПОК
# ─────────────────────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    user_id = query.from_user.id
    data    = query.data
    await query.answer()

    # ── Быстрое удаление из уведомления ──
    if data.startswith("del:"):
        if not is_admin(user_id):
            await query.answer("⛔ Только администратор.", show_alert=True)
            return
        tx_id = int(data.split(":")[1])
        tx    = get_transaction_by_id(tx_id)
        if not tx:
            await query.edit_message_text("❌ Запись не найдена (уже удалена?).")
            return
        delete_transaction(tx_id)
        sign = "+" if tx["amount"] > 0 else ""
        await query.edit_message_text(
            f"🗑 <b>Запись #{tx_id} удалена</b>\n"
            f"{sign}{fmt(tx['amount'], tx['currency'])} | {tx['comment'] or '—'}",
            parse_mode="HTML",
        )
        return

    # ── Навигация меню ──
    if data == "menu:main":
        if not is_allowed(user_id):
            return
        await query.edit_message_text(
            "Выберите действие:",
            reply_markup=main_menu_keyboard(user_id),
        )
        return

    if data == "menu:balance":
        if not is_allowed(user_id):
            return
        text = await send_balance_text(query.bot, query.message.chat_id)
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=back_keyboard("menu:main"),
        )
        return

    if data == "menu:reports":
        if not is_allowed(user_id):
            return
        await query.edit_message_text(
            "📊 <b>Выберите период отчёта:</b>",
            parse_mode="HTML",
            reply_markup=reports_keyboard(),
        )
        return

    if data == "menu:admin":
        if not is_admin(user_id):
            return
        recent = get_recent_transactions(3)
        lines  = ["🔧 <b>Панель администратора</b>\n"]
        if recent:
            lines.append("🕐 <b>Последние записи:</b>")
            for r in recent:
                sign = "+" if r["amount"] > 0 else ""
                lines.append(f"  <b>#{r['id']}</b> {sign}{fmt(r['amount'], r['currency'])} | {r['comment'] or '—'}")
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=admin_keyboard(),
        )
        return

    if data == "menu:help":
        if not is_allowed(user_id):
            return
        text = (
            "📋 <b>Справка по боту</b>\n\n"
            "<b>Запись в группе:</b>\n"
            "  <code>+600$ коммент</code>  → доход USD\n"
            "  <code>+500000 коммент</code> → доход UZS\n"
            "  <code>-150000 аренда</code>  → расход UZS\n"
            "  <code>50000</code>           → расход UZS\n\n"
            "<b>Команды в ЛС:</b>\n"
            "  /start — открыть меню\n"
        )
        if is_admin(user_id):
            text += (
                "\n<b>Удаление:</b> кнопка под уведомлением или 🔧 Управление\n"
                "<b>Редактирование комментария:</b> 🔧 Управление → ✏️\n"
                "<b>Дата начала учёта:</b> кнопка 📅 в меню\n"
            )
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=back_keyboard("menu:main"),
        )
        return

    # ── Отчёты ──
    if data.startswith("report:"):
        if not is_allowed(user_id):
            return
        today = date.today()
        period = data.split(":")[1]

        if period == "today":
            fd = td = today.strftime("%Y-%m-%d")
            label = f"Сегодня ({today.strftime('%d.%m.%Y')})"
        elif period == "week":
            fd    = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
            td    = today.strftime("%Y-%m-%d")
            label = "Текущая неделя"
        elif period == "month":
            fd    = today.strftime("%Y-%m-01")
            td    = today.strftime("%Y-%m-%d")
            label = f"Текущий месяц ({today.strftime('%m.%Y')})"
        elif period == "custom":
            context.user_data["awaiting"] = "custom_report"
            await query.edit_message_text(
                "✏️ <b>Введите период</b> в формате:\n\n"
                "<code>01.06-30.06</code>\n"
                "или\n"
                "<code>01.06.2025-30.06.2025</code>\n\n"
                "Или нажмите /start для отмены.",
                parse_mode="HTML",
            )
            return
        else:
            return

        text = build_report_text(fd, td, label)
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ К отчётам", callback_data="menu:reports")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="menu:main")],
            ]),
        )
        return

    # ── Панель администратора ──
    if data == "admin:recent":
        if not is_admin(user_id):
            return
        recent = get_recent_transactions(10)
        lines  = ["🕐 <b>Последние 10 записей:</b>\n"]
        for r in recent:
            sign = "+" if r["amount"] > 0 else ""
            dt   = r["created_at"][5:10]
            lines.append(
                f"<b>#{r['id']}</b> {dt}  {sign}{fmt(r['amount'], r['currency'])}"
                f"  | {r['username']}  | {r['comment'] or '—'}"
            )
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Назад", callback_data="menu:admin")],
            ]),
        )
        return

    if data == "admin:delete":
        if not is_admin(user_id):
            return
        context.user_data["awaiting"] = "delete_id"
        await query.edit_message_text(
            "🗑 <b>Удаление записи</b>\n\n"
            "Введите <b>ID записи</b> которую нужно удалить.\n"
            "(ID виден в уведомлениях и в списке последних записей)\n\n"
            "Или нажмите /start для отмены.",
            parse_mode="HTML",
        )
        return

    if data == "admin:edit":
        if not is_admin(user_id):
            return
        context.user_data["awaiting"] = "edit_id"
        await query.edit_message_text(
            "✏️ <b>Редактирование комментария</b>\n\n"
            "Введите <b>ID записи</b> которую нужно изменить.\n\n"
            "Или нажмите /start для отмены.",
            parse_mode="HTML",
        )
        return

    if data == "menu:setstart":
        if not is_admin(user_id):
            return
        current = get_start_date()
        current_str = f"<b>{current}</b>" if current else "<i>не задана (учитываются все записи)</i>"
        context.user_data["awaiting"] = "setstart"
        await query.edit_message_text(
            f"📅 <b>Дата начала учёта</b>\n\n"
            f"Текущая дата: {current_str}\n\n"
            f"Введите новую дату в формате <code>01.07.2025</code>\n"
            f"Или напишите <code>сброс</code> чтобы учитывать все записи.\n\n"
            f"Или нажмите /start для отмены.",
            parse_mode="HTML",
        )
        return


# ─────────────────────────────────────────────
# ОБРАБОТКА ТЕКСТА В ЛС (ответы на вопросы от кнопок)
# ─────────────────────────────────────────────
async def handle_private_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_allowed(user.id):
        return

    awaiting = context.user_data.get("awaiting")
    text     = update.message.text.strip()

    # ── Кастомный период отчёта ──
    if awaiting == "custom_report":
        context.user_data.pop("awaiting", None)
        today = date.today()
        if "-" not in text:
            await update.message.reply_text(
                "❌ Неверный формат. Пример: <code>01.06-30.06</code>",
                parse_mode="HTML",
                reply_markup=back_keyboard(),
            )
            return
        parts = text.split("-")
        def normalize(d):
            if d.count(".") == 1:
                d += f".{today.year}"
            return d
        fd = parse_date(normalize(parts[0]))
        td = parse_date(normalize(parts[1]))
        if not fd or not td:
            await update.message.reply_text(
                "❌ Неверный формат даты. Пример: <code>01.06-30.06</code>",
                parse_mode="HTML",
            )
            return
        report_text = build_report_text(fd, td, f"{parts[0]} — {parts[1]}")
        await update.message.reply_text(
            report_text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 Другой период", callback_data="menu:reports")],
                [InlineKeyboardButton("🏠 Главное меню",  callback_data="menu:main")],
            ]),
        )
        return

    # ── Удаление: ввод ID ──
    if awaiting == "delete_id":
        context.user_data.pop("awaiting", None)
        if not is_admin(user.id):
            return
        try:
            tx_id = int(text)
        except ValueError:
            await update.message.reply_text("❌ ID должен быть числом.", reply_markup=back_keyboard("menu:admin"))
            return
        tx = get_transaction_by_id(tx_id)
        if not tx:
            await update.message.reply_text(f"❌ Запись #{tx_id} не найдена.", reply_markup=back_keyboard("menu:admin"))
            return

        # Показываем запись с кнопкой подтверждения
        sign = "+" if tx["amount"] > 0 else ""
        await update.message.reply_text(
            f"🗑 <b>Удалить эту запись?</b>\n\n"
            f"<b>#{tx['id']}</b> | {sign}{fmt(tx['amount'], tx['currency'])}\n"
            f"👤 {tx['username']}\n"
            f"📝 {tx['comment'] or '—'}\n"
            f"📅 {tx['created_at'][:10]}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Да, удалить", callback_data=f"del:{tx_id}"),
                    InlineKeyboardButton("❌ Отмена",      callback_data="menu:admin"),
                ]
            ]),
        )
        return

    # ── Редактирование: ввод ID ──
    if awaiting == "edit_id":
        if not is_admin(user.id):
            return
        try:
            tx_id = int(text)
        except ValueError:
            context.user_data.pop("awaiting", None)
            await update.message.reply_text("❌ ID должен быть числом.", reply_markup=back_keyboard("menu:admin"))
            return
        tx = get_transaction_by_id(tx_id)
        if not tx:
            context.user_data.pop("awaiting", None)
            await update.message.reply_text(f"❌ Запись #{tx_id} не найдена.", reply_markup=back_keyboard("menu:admin"))
            return
        context.user_data["awaiting"]  = "edit_text"
        context.user_data["edit_tx_id"] = tx_id
        sign = "+" if tx["amount"] > 0 else ""
        await update.message.reply_text(
            f"✏️ <b>Запись #{tx_id}</b>\n"
            f"{sign}{fmt(tx['amount'], tx['currency'])} | Текущий коммент: <i>{tx['comment'] or '—'}</i>\n\n"
            f"Введите <b>новый комментарий</b>:",
            parse_mode="HTML",
        )
        return

    # ── Редактирование: ввод нового комментария ──
    if awaiting == "edit_text":
        if not is_admin(user.id):
            return
        context.user_data.pop("awaiting", None)
        tx_id = context.user_data.pop("edit_tx_id", None)
        if not tx_id:
            return
        edit_transaction_comment(tx_id, text)
        await update.message.reply_text(
            f"✅ <b>Запись #{tx_id} обновлена</b>\n"
            f"Новый комментарий: <i>{text}</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Назад в управление", callback_data="menu:admin")],
                [InlineKeyboardButton("🏠 Главное меню",        callback_data="menu:main")],
            ]),
        )
        return

    # ── Дата начала учёта ──
    if awaiting == "setstart":
        if not is_admin(user.id):
            return
        context.user_data.pop("awaiting", None)
        if text.lower() in ("сброс", "reset", "off"):
            set_start_date("")
            await update.message.reply_text(
                "✅ Дата начала сброшена. Учитываются все записи.",
                reply_markup=back_keyboard(),
            )
            return
        parsed = parse_date(text)
        if not parsed:
            await update.message.reply_text(
                "❌ Неверный формат. Пример: <code>01.07.2025</code>",
                parse_mode="HTML",
            )
            return
        set_start_date(parsed)
        await update.message.reply_text(
            f"✅ <b>Дата начала учёта: {parsed}</b>\n"
            f"Записи до этой даты игнорируются.",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )
        return

    # ── Ничего не ждём — показываем меню ──
    await update.message.reply_text(
        "Выберите действие:",
        reply_markup=main_menu_keyboard(user.id),
    )


# ─────────────────────────────────────────────
# ЗАПУСК
# ─────────────────────────────────────────────
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    private = filters.ChatType.PRIVATE
    groups  = filters.TEXT & filters.ChatType.GROUPS

    app.add_handler(CommandHandler("start",   start_command,      filters=private))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(private & filters.TEXT, handle_private_text))
    app.add_handler(MessageHandler(groups, handle_group_message))

    logger.info("Bot started (v3 — with buttons).")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
