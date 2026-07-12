"""Telegram handlers — полноценное меню с инлайн-кнопками."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from alerts.settings_db import UserSettingsDB

logger = logging.getLogger(__name__)

# ── Callback data ───────────────────────────────────────────────────

CB_MENU = "menu"
CB_SET = "set"
CB_STATUS = "status"
CB_STATS = "stats"
CB_HELP = "help"
CB_SIGNALS = "signals"
CB_BACK_PARAM = "back"  # back from choice to settings


def cb_set(key: str, value: str) -> str:
    """'set:exchange:BYBIT'."""
    return f"{CB_SET}:{key}:{value}"


# ── Keyboard builders ──────────────────────────────────────────────


def main_menu_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="📊 Статус", callback_data=CB_STATUS),
        InlineKeyboardButton(text="📈 RS-топ", callback_data="rs_top"),
    )
    b.row(
        InlineKeyboardButton(text="⚙️ Настройки", callback_data=CB_SET),
        InlineKeyboardButton(text="📈 Статистика", callback_data=CB_STATS),
    )
    b.row(
        InlineKeyboardButton(text="💡 Помощь", callback_data=CB_HELP),
        InlineKeyboardButton(text="🔔 Сигналы", callback_data=CB_SIGNALS),
    )
    return b.as_markup()


def settings_keyboard(s: dict) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(
        text=f"🏦 Биржа: {s['exchange']}",
        callback_data=cb_set("exchange", ""),
    ))
    b.row(InlineKeyboardButton(
        text=f"📊 Рынок: {s['market']}",
        callback_data=cb_set("market", ""),
    ))
    b.row(InlineKeyboardButton(
        text=f"📈 Порог: {s['threshold_pct']:.0f}%",
        callback_data=cb_set("threshold_pct", ""),
    ))
    b.row(InlineKeyboardButton(
        text=f"⏱️ Таймфрейм: {s['timeframe']}",
        callback_data=cb_set("timeframe", ""),
    ))
    b.row(InlineKeyboardButton(
        text=f"🎯 Min Score: {s['min_score']}",
        callback_data=cb_set("min_score", ""),
    ))
    b.row(InlineKeyboardButton(text="🔙 В меню", callback_data=CB_MENU))
    return b.as_markup()


def choice_keyboard(key: str, current: str, options: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for label, value in options:
        mark = "✅ " if value == current else ""
        b.row(InlineKeyboardButton(text=f"{mark}{label}", callback_data=cb_set(key, value)))
    b.row(
        InlineKeyboardButton(text="🔙 Назад", callback_data=CB_BACK_PARAM),
        InlineKeyboardButton(text="🔙 В меню", callback_data=CB_MENU),
    )
    return b.as_markup()


# ── Settings options ───────────────────────────────────────────────

EXCHANGE_OPTIONS = [("BYBIT", "BYBIT"), ("BINANCE", "BINANCE")]
MARKET_OPTIONS = [("Фьючерсы", "FUTURE"), ("Спот", "SPOT")]
THRESHOLD_OPTIONS = [("1.0%", "1.0"), ("2.0%", "2.0"), ("3.0%", "3.0"), ("5.0%", "5.0")]
TIMEFRAME_OPTIONS = [
    ("1m", "1m"), ("3m", "3m"), ("5m", "5m"),
    ("15m", "15m"), ("30m", "30m"), ("1h", "1h"), ("4h", "4h"),
]
MIN_SCORE_OPTIONS = [("40 (Все)", "40"), ("60 (Средние)", "60"), ("80 (Сильные)", "80")]

CHOICE_MENUS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "exchange": ("🏦 Биржа", EXCHANGE_OPTIONS),
    "market": ("📊 Рынок", MARKET_OPTIONS),
    "threshold_pct": ("📈 Порог изменения", THRESHOLD_OPTIONS),
    "timeframe": ("⏱️ Таймфрейм", TIMEFRAME_OPTIONS),
    "min_score": ("🎯 Минимальный Score", MIN_SCORE_OPTIONS),
}

# ── Router ─────────────────────────────────────────────────────────

router = Router()


def setup_telegram_handlers(settings_db: UserSettingsDB, notifier=None):
    router.message.register(cmd_start, Command("start"))
    router.message.register(cmd_menu, Command("menu"))
    router.message.register(cmd_help, Command("help"))
    router.message.register(cmd_settings, Command("settings"))
    router.callback_query.register(callback_handler)

    router.settings_db = settings_db
    router.notifier = notifier

    logger.info("Telegram handlers registered (menu)")
    return router


# ── Format helpers ─────────────────────────────────────────────────


def _fmt_settings(s: dict) -> str:
    return (
        f"⚙️ <b>Ваши настройки</b> (✨ Premium)\n\n"
        f"🏦 Биржа: <b>{s['exchange']}</b>\n"
        f"📊 Рынок: <b>{s['market']}</b>\n"
        f"📈 Порог: <b>{s['threshold_pct']:.0f}%</b>\n"
        f"⏱️ Таймфрейм: <b>{s['timeframe']}</b>\n"
        f"🎯 Min Score: <b>{s['min_score']}</b>"
    )


def _fmt_status() -> str:
    health = getattr(router, "_health_cache", {})
    lines = ["📊 <b>Статус скринера</b>\n"]
    if health:
        lines.append(f"🕐 <b>Аптайм:</b> {health.get('uptime', '?')}")
        lines.append(f"📡 <b>Сигналов:</b> {health.get('signals', '?')}")
        lines.append(f"🌍 <b>Сессия:</b> {health.get('session', '?')}")
        lines.append(f"🔗 <b>Корреляция:</b> {health.get('corr_alignment', '?')}")
        lines.append(f"📈 <b>RS топ:</b> {health.get('rs_top', '?')}")
        lines.append(f"📂 <b>Сектор:</b> {health.get('sector', '?')}")
        lines.append(f"📊 <b>Ширина:</b> {health.get('breadth', '?')} зелёных")
        lines.append(f"⚡ <b>Событий:</b> {health.get('events', '?')}")
        lines.append(f"💾 <b>БД:</b> {health.get('db_status', '?')}")
        lines.append(f"🏦 <b>Биржа:</b> {health.get('exchange', 'Bybit')}")
    else:
        lines.append("🔄 Данные накапливаются... Попробуй через минуту.")
    return "\n".join(lines)


def _fmt_rs_top() -> str:
    health = getattr(router, "_health_cache", {})
    rs_raw = health.get("rs_top", "") or ""
    align = health.get("corr_alignment", "?")
    sector = health.get("sector", "?")
    lines = [
        "📈 <b>Relative Strength</b>\n",
        f"🥇 <b>Топ RS:</b> {rs_raw}",
        f"🔗 <b>Корреляция:</b> {align}",
        f"📂 <b>Сектор:</b> {sector}",
    ]
    return "\n".join(lines)


def _fmt_help() -> str:
    return (
        "<b>🤖 Crypto Screener v2</b>\n\n"
        "Я мониторю топ‑10 криптопар на Bybit в реальном времени "
        "и присылаю сигналы.\n\n"
        "<b>🔔 Сигналы:</b>\n"
        "• Приходят автоматически при срабатывании\n"
        "• Частота зависит от Min Score в настройках\n"
        "• Anti-spam: повтор не чаще 1 раза в 30 мин\n\n"
        "<b>📡 Данные:</b>\n"
        "• Свечи 1m / 5m / 15m — 10 монет\n"
        "• Trades — поток сделок\n"
        "• Orderbook — стакан BTC и ETH\n"
        "• Ликвидации — все монеты\n"
        "• Correlation Engine\n"
        "• Relative Strength\n"
        "• Sector Scanner (6 секторов)\n\n"
        "<b>Команды:</b>\n"
        "/start — Меню\n"
        "/settings — Настройки\n"
        "/menu — Главное меню\n"
        "/help — Это сообщение"
    )


# ── Command handlers ───────────────────────────────────────────────


async def cmd_start(message: Message):
    sdb: UserSettingsDB = router.settings_db
    sdb.get(message.chat.id)  # создаёт запись если нет
    await message.answer(
        f"🚀 <b>Crypto Screener v2</b>\n\n"
        f"Привет, {message.from_user.first_name or 'трейдер'}! "
        f"Отслеживаю топ‑10 криптопар, присылаю сигналы.",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


async def cmd_menu(message: Message):
    await message.answer("📌 <b>Главное меню</b>", parse_mode="HTML", reply_markup=main_menu_keyboard())


async def cmd_help(message: Message):
    await message.answer(_fmt_help(), parse_mode="HTML", reply_markup=main_menu_keyboard())


async def cmd_settings(message: Message):
    sdb: UserSettingsDB = router.settings_db
    s = sdb.get(message.chat.id)
    await message.answer(
        _fmt_settings(s),
        parse_mode="HTML",
        reply_markup=settings_keyboard(s),
    )


# ── Callback handler ───────────────────────────────────────────────


async def callback_handler(call: CallbackQuery):
    data = call.data
    sdb: UserSettingsDB = router.settings_db
    chat_id = call.message.chat.id
    s = sdb.get(chat_id)

    # ── Главное меню ──
    if data == CB_MENU:
        await call.message.edit_text(
            "📌 <b>Главное меню</b>", parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
        await call.answer()
        return

    if data == CB_STATUS:
        await call.message.edit_text(
            _fmt_status(), parse_mode="HTML",
            reply_markup=_menu_back("📊 Статус"),
        )
        await call.answer()
        return

    if data == "rs_top":
        await call.message.edit_text(
            _fmt_rs_top(), parse_mode="HTML",
            reply_markup=_menu_back("📈 RS-топ"),
        )
        await call.answer()
        return

    if data == CB_STATS:
        stats = getattr(router, "_stats_cache", {})
        if stats:
            total = stats.get("total", 0)
            wins = stats.get("wins", 0)
            losses = stats.get("losses", 0)
            wr = (wins / total * 100) if total else 0
            by_type = stats.get("by_type", {})
            lines = [f"📈 <b>Статистика</b>\n"]
            lines.append(f"📊 Всего: <b>{total}</b>")
            lines.append(f"✅ Винрейт: <b>{wr:.1f}%</b> ({wins}/{wins + losses})\n")
            lines.append("<b>По типам:</b>")
            for tname, cnt in sorted(by_type.items(), key=lambda x: -x[1])[:10]:
                lines.append(f"  • {tname}: {cnt}")
            text = "\n".join(lines)
        else:
            text = "📈 <b>Статистика</b>\n\n🔄 Данные накапливаются... (нужно >30 мин работы)"
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=_menu_back("📈 Статистика"))
        await call.answer()
        return

    if data == CB_HELP:
        await call.message.edit_text(_fmt_help(), parse_mode="HTML", reply_markup=_menu_back("💡 Помощь"))
        await call.answer()
        return

    if data == CB_SIGNALS:
        recent = getattr(router, "_recent_signals", [])
        if recent:
            lines = ["🔔 <b>Последние сигналы</b>\n"]
            for sig in recent[-10:]:
                emoji = "🟢" if sig.get("direction") == "buy" else ("🔴" if sig.get("direction") == "sell" else "⚪")
                lines.append(f"{emoji} <b>{sig.get('symbol', '?')}</b> — "
                             f"{sig.get('signal_name', '?')} ({sig.get('score', 0)}/100)")
            text = "\n".join(lines)
        else:
            text = "🔔 <b>Сигналы</b>\n\nПока нет сигналов. Ожидай..."
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=_menu_back("🔔 Сигналы"))
        await call.answer()
        return

    # ── Settings / choice back ──
    if data == CB_SET or data == CB_BACK_PARAM:
        await call.message.edit_text(
            _fmt_settings(s), parse_mode="HTML",
            reply_markup=settings_keyboard(s),
        )
        await call.answer()
        return

    # ── Choice sub‑menu ──
    if data.startswith(CB_SET + ":"):
        _, key, value = data.split(":", 2)
        if not value:
            title, options = CHOICE_MENUS[key]
            current = str(s.get(key, ""))
            if isinstance(s.get(key), float):
                current = f"{s[key]:.1f}"
            await call.message.edit_text(
                f"<b>{title}</b>\n\nВыбери значение:",
                parse_mode="HTML",
                reply_markup=choice_keyboard(key, current, options),
            )
            await call.answer()
            return

        # Save chosen value
        typed: str | float | int = value
        if key == "threshold_pct":
            typed = float(value)
        elif key == "min_score":
            typed = int(value)
        sdb.set(chat_id, key, typed)
        s = sdb.get(chat_id)
        await call.message.edit_text(
            _fmt_settings(s), parse_mode="HTML",
            reply_markup=settings_keyboard(s),
        )
        await call.answer(f"✅ {key} = {value}")
        return

    await call.answer("❓ Неизвестная команда")


def _menu_back(label: str) -> InlineKeyboardMarkup:
    """Кнопка «в меню» после просмотра раздела."""
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🔙 В меню", callback_data=CB_MENU))
    return b.as_markup()
