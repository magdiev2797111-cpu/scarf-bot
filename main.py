import asyncio
import json
import logging
import os
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Tuple

from dotenv import load_dotenv
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else None

ORDERS_FILE = Path("orders.json")

MENU_NEW_ORDER = "Новый заказ"
MENU_TODAY = "Заказы за сегодня"
MENU_TO_SEND = "Что отправить"
MENU_CHANGE_STATUS = "Изменить статус"
MENU_REPORT_TODAY = "Отчет за сегодня"
MENU_REPORT_MONTH = "Отчет за месяц"
MENU_TOP_MODELS_MONTH = "Топ моделей за месяц"

DELIVERY_OPTIONS = ["СДЭК", "Почта", "Самовывоз"]
STATUSES = ["Новый", "Подтвержден", "Оплачен", "Отправлен", "Завершен", "Отмена"]

(
    MODEL,
    QTY,
    AMOUNT,
    CLIENT_NAME,
    PHONE,
    DELIVERY,
    COMMENT,
    SELECT_ORDER_STATUS,
    SELECT_NEW_STATUS,
) = range(9)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [MENU_NEW_ORDER, MENU_TODAY],
            [MENU_TO_SEND, MENU_CHANGE_STATUS],
            [MENU_REPORT_TODAY, MENU_REPORT_MONTH],
            [MENU_TOP_MODELS_MONTH],
        ],
        resize_keyboard=True,
    )


def delivery_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[option] for option in DELIVERY_OPTIONS],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def status_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[status] for status in STATUSES],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def ensure_orders_file() -> None:
    if not ORDERS_FILE.exists():
        ORDERS_FILE.write_text("[]", encoding="utf-8")


def load_orders() -> List[Dict[str, Any]]:
    ensure_orders_file()
    try:
        return json.loads(ORDERS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def save_orders(orders: List[Dict[str, Any]]) -> None:
    ORDERS_FILE.write_text(
        json.dumps(orders, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def next_order_id(orders: List[Dict[str, Any]]) -> int:
    if not orders:
        return 1
    return max(int(order.get("id", 0)) for order in orders) + 1


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def format_rub(value: float) -> str:
    rounded = round(value, 2)
    if rounded.is_integer():
        return str(int(rounded))
    return f"{rounded:.2f}".replace(".", ",")


def parse_order_date(order: Dict[str, Any]) -> date | None:
    created_date = str(order.get("created_date", "")).strip()
    if created_date:
        try:
            return datetime.strptime(created_date, "%Y-%m-%d").date()
        except ValueError:
            pass

    created_at = str(order.get("created_at", "")).strip()
    if created_at:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(created_at, fmt).date()
            except ValueError:
                continue

    return None


def filter_today_orders(orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    today = datetime.now().date()
    return [order for order in orders if parse_order_date(order) == today]


def filter_current_month_orders(orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    now = datetime.now()
    result: List[Dict[str, Any]] = []
    for order in orders:
        d = parse_order_date(order)
        if d and d.year == now.year and d.month == now.month:
            result.append(order)
    return result


def filter_status(orders: List[Dict[str, Any]], status: str) -> List[Dict[str, Any]]:
    return [order for order in orders if str(order.get("status", "")).strip() == status]


def calc_summary(orders: List[Dict[str, Any]]) -> Dict[str, float]:
    orders_count = len(orders)
    items_count = sum(max(safe_int(order.get("quantity", 0)), 0) for order in orders)
    total_amount = sum(max(safe_float(order.get("amount_rub", 0)), 0.0) for order in orders)
    avg_check = total_amount / orders_count if orders_count else 0.0

    return {
        "orders_count": float(orders_count),
        "items_count": float(items_count),
        "total_amount": total_amount,
        "avg_check": avg_check,
    }


def count_status(orders: List[Dict[str, Any]], status: str) -> int:
    return sum(1 for order in orders if str(order.get("status", "")).strip() == status)


def render_split_line(title: str, orders: List[Dict[str, Any]]) -> str:
    s = calc_summary(orders)
    return (
        f"{title}: {int(s['orders_count'])} заказов, "
        f"{int(s['items_count'])} шт, "
        f"{format_rub(s['total_amount'])} ₽"
    )


def collect_model_totals(orders: List[Dict[str, Any]]) -> List[Tuple[str, int]]:
    totals: Dict[str, int] = {}
    for order in orders:
        model = str(order.get("model", "")).strip() or "Без модели"
        qty = max(safe_int(order.get("quantity", 0)), 0)
        totals[model] = totals.get(model, 0) + qty

    return sorted(totals.items(), key=lambda pair: pair[1], reverse=True)


def render_model_top_block(title: str, totals: List[Tuple[str, int]]) -> str:
    if not totals:
        return f"{title}:\n- Нет данных."

    lines = [f"{title}:"]
    for idx, (model, qty) in enumerate(totals, start=1):
        lines.append(f"{idx}. {model} - {qty} шт")
    return "\n".join(lines)


def check_admin(update: Update) -> bool:
    if ADMIN_ID is None:
        return True
    user = update.effective_user
    return bool(user and user.id == ADMIN_ID)


def format_order_short(order: Dict[str, Any]) -> str:
    return (
        f"#{order['id']} | {order['model']} | {order['quantity']} шт | "
        f"{order['amount_rub']} ₽ | {order['status']}"
    )


def format_order_full(order: Dict[str, Any]) -> str:
    return (
        f"Заказ #{order['id']}\n"
        f"Модель: {order['model']}\n"
        f"Количество: {order['quantity']}\n"
        f"Сумма: {order['amount_rub']} ₽\n"
        f"Клиент: {order['client_name']}\n"
        f"Телефон: {order['phone']}\n"
        f"Доставка: {order['delivery']}\n"
        f"Комментарий: {order['comment'] or '-'}\n"
        f"Статус: {order['status']}\n"
        f"Создан: {order['created_at']}"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not check_admin(update):
        await update.message.reply_text("Доступ запрещен.")
        return

    context.user_data.clear()
    await update.message.reply_text("Главное меню:", reply_markup=main_menu_keyboard())


async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not check_admin(update):
        await update.message.reply_text("Доступ запрещен.")
        return ConversationHandler.END

    text = (update.message.text or "").strip()

    if text == MENU_NEW_ORDER:
        context.user_data["new_order"] = {}
        await update.message.reply_text(
            "Введите модель платка:",
            reply_markup=ReplyKeyboardRemove(),
        )
        return MODEL

    if text == MENU_TODAY:
        await show_today_orders(update)
        return ConversationHandler.END

    if text == MENU_TO_SEND:
        await show_to_send(update)
        return ConversationHandler.END

    if text == MENU_CHANGE_STATUS:
        return await start_change_status(update, context)

    if text == MENU_REPORT_TODAY:
        await show_report_today(update)
        return ConversationHandler.END

    if text == MENU_REPORT_MONTH:
        await show_report_month(update)
        return ConversationHandler.END

    if text == MENU_TOP_MODELS_MONTH:
        await show_top_models_month(update)
        return ConversationHandler.END

    await update.message.reply_text(
        "Выберите действие через кнопки меню.",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


async def show_today_orders(update: Update) -> None:
    orders = load_orders()
    today_orders = filter_today_orders(orders)

    if not today_orders:
        await update.message.reply_text("Сегодня заказов нет.", reply_markup=main_menu_keyboard())
        return

    text = "Заказы за сегодня:\n\n" + "\n\n".join(format_order_full(order) for order in today_orders)
    await update.message.reply_text(text, reply_markup=main_menu_keyboard())


async def show_to_send(update: Update) -> None:
    orders = load_orders()
    to_send = filter_status(orders, "Оплачен")

    if not to_send:
        await update.message.reply_text(
            "Нет заказов для отправки (со статусом 'Оплачен').",
            reply_markup=main_menu_keyboard(),
        )
        return

    text = "Что отправить:\n\n" + "\n\n".join(format_order_full(order) for order in to_send)
    await update.message.reply_text(text, reply_markup=main_menu_keyboard())


async def show_report_today(update: Update) -> None:
    orders = filter_today_orders(load_orders())
    summary = calc_summary(orders)

    paid_count = count_status(orders, "Оплачен")
    sent_count = count_status(orders, "Отправлен")
    cancel_count = count_status(orders, "Отмена")

    paid_orders = filter_status(orders, "Оплачен")
    sent_orders = filter_status(orders, "Отправлен")

    report = (
        "Отчет за сегодня:\n"
        f"- Количество заказов: {int(summary['orders_count'])}\n"
        f"- Количество товаров: {int(summary['items_count'])}\n"
        f"- Общая сумма: {format_rub(summary['total_amount'])} ₽\n"
        f"- Сколько оплачено: {paid_count}\n"
        f"- Сколько отправлено: {sent_count}\n"
        f"- Сколько отменено: {cancel_count}\n"
        f"- Средний чек: {format_rub(summary['avg_check'])} ₽\n\n"
        "Считать отдельно:\n"
        f"- {render_split_line('Все заказы', orders)}\n"
        f"- {render_split_line('Оплаченные', paid_orders)}\n"
        f"- {render_split_line('Отправленные', sent_orders)}"
    )

    await update.message.reply_text(report, reply_markup=main_menu_keyboard())


async def show_report_month(update: Update) -> None:
    orders = filter_current_month_orders(load_orders())
    summary = calc_summary(orders)

    paid_count = count_status(orders, "Оплачен")
    sent_count = count_status(orders, "Отправлен")
    done_count = count_status(orders, "Завершен")
    cancel_count = count_status(orders, "Отмена")

    paid_orders = filter_status(orders, "Оплачен")
    sent_orders = filter_status(orders, "Отправлен")

    report = (
        "Отчет за месяц:\n"
        f"- Количество заказов: {int(summary['orders_count'])}\n"
        f"- Количество товаров: {int(summary['items_count'])}\n"
        f"- Общая сумма: {format_rub(summary['total_amount'])} ₽\n"
        f"- Сколько оплачено: {paid_count}\n"
        f"- Сколько отправлено: {sent_count}\n"
        f"- Сколько завершено: {done_count}\n"
        f"- Сколько отменено: {cancel_count}\n"
        f"- Средний чек: {format_rub(summary['avg_check'])} ₽\n\n"
        "Считать отдельно:\n"
        f"- {render_split_line('Все заказы', orders)}\n"
        f"- {render_split_line('Оплаченные', paid_orders)}\n"
        f"- {render_split_line('Отправленные', sent_orders)}"
    )

    await update.message.reply_text(report, reply_markup=main_menu_keyboard())


async def show_top_models_month(update: Update) -> None:
    month_orders = filter_current_month_orders(load_orders())
    paid_orders = filter_status(month_orders, "Оплачен")
    sent_orders = filter_status(month_orders, "Отправлен")

    all_totals = collect_model_totals(month_orders)
    paid_totals = collect_model_totals(paid_orders)
    sent_totals = collect_model_totals(sent_orders)

    report = (
        "Топ моделей за месяц (по количеству штук):\n\n"
        f"{render_model_top_block('Все заказы', all_totals)}\n\n"
        f"{render_model_top_block('Оплаченные', paid_totals)}\n\n"
        f"{render_model_top_block('Отправленные', sent_totals)}"
    )

    await update.message.reply_text(report, reply_markup=main_menu_keyboard())


async def set_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    model = (update.message.text or "").strip()
    if not model:
        await update.message.reply_text("Введите модель платка текстом.")
        return MODEL

    context.user_data["new_order"]["model"] = model
    await update.message.reply_text("Введите количество (целое число):")
    return QTY


async def set_qty(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("Количество должно быть положительным целым числом.")
        return QTY

    context.user_data["new_order"]["quantity"] = int(text)
    await update.message.reply_text("Введите сумму в рублях (только число):")
    return AMOUNT


async def set_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").replace(",", ".").strip()
    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Сумма должна быть положительным числом.")
        return AMOUNT

    context.user_data["new_order"]["amount_rub"] = round(amount, 2)
    await update.message.reply_text("Введите имя клиента:")
    return CLIENT_NAME


async def set_client_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text("Имя не может быть пустым.")
        return CLIENT_NAME

    context.user_data["new_order"]["client_name"] = name
    await update.message.reply_text("Введите телефон клиента:")
    return PHONE


async def set_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    phone = (update.message.text or "").strip()
    if len(phone) < 5:
        await update.message.reply_text("Введите корректный телефон.")
        return PHONE

    context.user_data["new_order"]["phone"] = phone
    await update.message.reply_text("Выберите способ доставки:", reply_markup=delivery_keyboard())
    return DELIVERY


async def set_delivery(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    delivery = (update.message.text or "").strip()
    if delivery not in DELIVERY_OPTIONS:
        await update.message.reply_text("Выберите доставку кнопкой: СДЭК, Почта или Самовывоз.")
        return DELIVERY

    context.user_data["new_order"]["delivery"] = delivery
    await update.message.reply_text(
        "Введите комментарий (или '-' если без комментария):",
        reply_markup=ReplyKeyboardRemove(),
    )
    return COMMENT


async def set_comment_and_finish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    comment = (update.message.text or "").strip()
    if comment == "-":
        comment = ""

    draft = context.user_data.get("new_order", {})
    now = datetime.now()

    orders = load_orders()
    order = {
        "id": next_order_id(orders),
        "model": draft.get("model", ""),
        "quantity": draft.get("quantity", 0),
        "amount_rub": draft.get("amount_rub", 0),
        "client_name": draft.get("client_name", ""),
        "phone": draft.get("phone", ""),
        "delivery": draft.get("delivery", ""),
        "comment": comment,
        "status": "Новый",
        "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "created_date": now.date().isoformat(),
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
    }
    orders.append(order)
    save_orders(orders)
    context.user_data.clear()

    await update.message.reply_text(
        f"Заказ создан.\n\n{format_order_full(order)}",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


async def start_change_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    orders = load_orders()
    if not orders:
        await update.message.reply_text("Заказов пока нет.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    last_orders = sorted(orders, key=lambda x: int(x.get("id", 0)), reverse=True)[:15]
    buttons = [[str(order["id"])] for order in last_orders]
    text = "Выберите ID заказа для смены статуса:\n" + "\n".join(
        format_order_short(order) for order in last_orders
    )
    await update.message.reply_text(
        text,
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=True),
    )
    return SELECT_ORDER_STATUS


async def select_order_for_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if not text.isdigit():
        await update.message.reply_text("Введите ID заказа числом.")
        return SELECT_ORDER_STATUS

    order_id = int(text)
    orders = load_orders()
    target = next((o for o in orders if int(o.get("id", 0)) == order_id), None)
    if not target:
        await update.message.reply_text("Заказ с таким ID не найден. Попробуйте снова.")
        return SELECT_ORDER_STATUS

    context.user_data["status_order_id"] = order_id
    await update.message.reply_text(
        f"Текущий статус заказа #{order_id}: {target.get('status', '-')}\nВыберите новый статус:",
        reply_markup=status_keyboard(),
    )
    return SELECT_NEW_STATUS


async def select_new_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_status = (update.message.text or "").strip()
    if new_status not in STATUSES:
        await update.message.reply_text("Выберите статус кнопкой.")
        return SELECT_NEW_STATUS

    order_id = context.user_data.get("status_order_id")
    orders = load_orders()
    target = next((o for o in orders if int(o.get("id", 0)) == int(order_id)), None)
    if not target:
        await update.message.reply_text("Заказ не найден.", reply_markup=main_menu_keyboard())
        context.user_data.pop("status_order_id", None)
        return ConversationHandler.END

    target["status"] = new_status
    target["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_orders(orders)

    await update.message.reply_text(
        f"Статус заказа #{order_id} обновлен: {new_status}",
        reply_markup=main_menu_keyboard(),
    )
    context.user_data.pop("status_order_id", None)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Действие отменено.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


def build_app() -> Application:
    if not BOT_TOKEN:
        raise ValueError("Не найден BOT_TOKEN. Добавьте его в .env")

    app = Application.builder().token(BOT_TOKEN).build()

    create_order_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{MENU_NEW_ORDER}$"), menu_router)],
        states={
            MODEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_model)],
            QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_qty)],
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_amount)],
            CLIENT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_client_name)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_phone)],
            DELIVERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_delivery)],
            COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_comment_and_finish)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    status_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{MENU_CHANGE_STATUS}$"), menu_router)],
        states={
            SELECT_ORDER_STATUS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, select_order_for_status)
            ],
            SELECT_NEW_STATUS: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_new_status)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(create_order_conv)
    app.add_handler(status_conv)
    app.add_handler(MessageHandler(filters.Regex(f"^{MENU_TODAY}$"), menu_router))
    app.add_handler(MessageHandler(filters.Regex(f"^{MENU_TO_SEND}$"), menu_router))
    app.add_handler(MessageHandler(filters.Regex(f"^{MENU_REPORT_TODAY}$"), menu_router))
    app.add_handler(MessageHandler(filters.Regex(f"^{MENU_REPORT_MONTH}$"), menu_router))
    app.add_handler(MessageHandler(filters.Regex(f"^{MENU_TOP_MODELS_MONTH}$"), menu_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))

    return app


def main() -> None:
    ensure_orders_file()
    app = build_app()

    # Python 3.14 may not have a default loop in MainThread.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    app.run_polling()


if __name__ == "__main__":
    main()
