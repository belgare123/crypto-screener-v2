"""
Telegram alerts — отправка сигналов и меню.

v0.9.0:
  - Retry/backoff при ошибках (экспоненциальная задержка)
  - Обработка Retry-After от Telegram (429)
  - signal_to_text() через реестр форматтеров (без elif-цепочек)
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
from typing import Any, Callable

from core import SignalResult
from utils import fmt_usdt, fmt_percent

logger = logging.getLogger(__name__)

try:
    from aiogram import Bot, Dispatcher
    from aiogram.types import Message
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    HAS_AIOGRAM = True
except ImportError:
    HAS_AIOGRAM = False

    class Bot:  # type: ignore[no-redef]
        pass


# ── Retry / Backoff ─────────────────────────────────────────────────

MAX_RETRIES = 3
BASE_DELAY = 2.0  # seconds


def _extract_retry_after(exc: Exception) -> float | None:
    """Извлечь Retry-After из исключения aiogram или HTTP."""
    err_msg = str(exc)
    # aiogram: код 429 с retry_after
    if hasattr(exc, "retry_after"):
        return float(exc.retry_after)
    # HTTP fallback: ищем retry_after в теле/заголовке
    import re
    m = re.search(r"retry_after[=:\s]+(\d+)", err_msg, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


async def _send_with_retry(
    fn: Callable, *args, **kwargs
) -> Any:
    """Вызвать fn с экспоненциальным backoff."""
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            retry_after = _extract_retry_after(exc)
            wait = retry_after or (BASE_DELAY ** (attempt + 1))
            if attempt == MAX_RETRIES - 1:
                logger.error(
                    "[tg] send failed after %d retries: %s",
                    MAX_RETRIES, exc,
                )
                raise
            logger.warning(
                "[tg] attempt %d/%d failed, retrying in %.1fs: %s",
                attempt + 1, MAX_RETRIES, wait, exc,
            )
            if retry_after:
                # 429 — строгий backoff
                await asyncio.sleep(wait)
            else:
                await asyncio.sleep(wait)
    raise last_exc  # type: ignore[misc]


# ── Formatting helpers ──────────────────────────────────────────────

PRIORITY_MAP: list[tuple[tuple[int, int], str]] = [
    ((95, 100), "🔥 CRITICAL"),
    ((80, 95), "🚀 STRONG"),
    ((60, 80), "✅ NORMAL"),
    ((40, 60), "⚪ WEAK"),
]


def _priority_label(score: float) -> str:
    for (lo, hi), label in PRIORITY_MAP:
        if lo <= score < hi:
            return label
    return "⚪ WEAK"


def _escape_md(text: str) -> str:
    """Escape Telegram Markdown V1 special chars."""
    return text.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")


def _fmt_volume(vol: float) -> str:
    """Форматировать объём: 1.5M, 234K, 1.2B."""
    if vol >= 1_000_000_000:
        return f"{vol / 1_000_000_000:.1f}B"
    if vol >= 1_000_000:
        return f"{vol / 1_000_000:.1f}M"
    if vol >= 1_000:
        return f"{vol / 1_000:.0f}K"
    return f"{vol:.0f}"


# ── Signal formatters (registry) ───────────────────────────────────
# Каждый форматтер получает SignalResult, возвращает list[str].
# Имя форматтера = signal_name.

_SIGNAL_FORMATTERS: dict[str, Callable[[SignalResult], list[str]]] = {}


def register_formatter(signal_name: str):
    """Декоратор для регистрации форматтера сигнала."""
    def decorator(fn: Callable[[SignalResult], list[str]]):
        _SIGNAL_FORMATTERS[signal_name] = fn
        return fn
    return decorator


# ── Whale ──

@register_formatter("whale")
def _fmt_whale(sig: SignalResult) -> list[str]:
    meta = sig.meta
    val = meta.get("value", 0)
    price = meta.get("price", 0)
    side = meta.get("side", "buy")
    ex = meta.get("exchange", "")
    return [
        f"  💰 {fmt_usdt(val)} | Price: ${price:.2f} | {ex}",
        f"  Side: {'🟢 Buy' if side == 'buy' else '🔴 Sell'}",
    ]


# ── Volume Spike ──

@register_formatter("volume_spike")
def _fmt_volume_spike(sig: SignalResult) -> list[str]:
    meta = sig.meta
    mult = meta.get("multiplier", 0)
    vol = meta.get("volume", 0)
    z = meta.get("z_score", 0)
    return [f"  📊 {mult}× avg volume | Z‑score: {z}"]


# ── Smart Money ──

@register_formatter("smart_money")
def _fmt_smart_money(sig: SignalResult) -> list[str]:
    meta = sig.meta
    factors = meta.get("factors", [])
    lines = []
    for name, score, val in factors:
        icon = "🟢" if score > 0 else "🔴"
        lines.append(f"  {icon} {_escape_md(str(name))}: {val}")
    lines.append(f"  Factors: {meta.get('factor_count', 0)}")
    return lines


# ── AI Score ──

@register_formatter("ai_score")
def _fmt_ai_score(sig: SignalResult) -> list[str]:
    meta = sig.meta
    ai_score = meta.get("ai_score", 0)
    confidence = meta.get("confidence", 0)
    regime = meta.get("regime", "?")
    regime_icon = meta.get("regime_icon", "❓")
    factors = meta.get("factors", [])
    depth = meta.get("depth", {})
    agreement = meta.get("agreement", 0)
    buy_score = meta.get("buy_score", 0)
    sell_score = meta.get("sell_score", 0)

    # Confidence bar
    if confidence >= 80:
        conf_label = "🔥 High"
    elif confidence >= 60:
        conf_label = "✅ Moderate"
    elif confidence >= 40:
        conf_label = "⚠️ Low"
    else:
        conf_label = "❌ Very Low"

    # Score bar (visual gauge)
    score_gauge = "█" * int(ai_score / 10) + "░" * (10 - int(ai_score / 10))

    lines = [
        f"  **AI Score**: {ai_score:.0f}/100  [{score_gauge}]",
        f"  **Confidence**: {confidence:.0f}% — {conf_label}",
        f"  **Market**: {regime_icon} {_escape_md(regime)}",
    ]

    # Extra regime details
    atr_pct = depth.get("atr_pct")
    vol_pct = depth.get("vol_pct")
    if atr_pct is not None:
        lines.append(f"  Volatility: {atr_pct:.2f}% ATR | Vol: {vol_pct:.1f}× avg")

    # Factor breakdown
    if factors:
        lines.append("")
        for name, score, icon in factors:
            bar = "🟢" if score >= 0 else "🔴"
            lines.append(f"  {icon} {_escape_md(str(name))}: {score:.0f}")
        lines.append(f"  ─────────────────")
        lines.append(f"  Buy: {buy_score:.0f} | Sell: {sell_score:.0f}")
        lines.append(f"  Agreement: {agreement:.0f}%")

    # Multi-Timeframe Confluence
    confluence = meta.get("confluence")
    if confluence:
        stars = confluence.get("stars", 0)
        details = confluence.get("details", [])
        star_chars = "★" * stars + "☆" * (3 - stars)
        dir_emojis = {"buy": "🟢", "sell": "🔴", "neutral": "⚪"}
        tf_line = " | ".join(
            f"{d['tf']} {dir_emojis.get(d['dir'], '⚪')} {d['dir'].upper()}"
            for d in details
        )
        lines.append(f"  🌐 MTF: {tf_line}")
        lines.append(f"         Confluence {star_chars}")

    return lines


# ── Market Breadth ──

@register_formatter("market_breadth")
def _fmt_market_breadth(sig: SignalResult) -> list[str]:
    meta = sig.meta
    pct_green = meta.get("pct_green", 0)
    pct_red = meta.get("pct_red", 0)
    growing = meta.get("growing", 0)
    falling = meta.get("falling", 0)
    total = meta.get("total", 0)
    regime = meta.get("regime", "neutral")
    avg = meta.get("avg_change", 0)
    desc = meta.get("description", "")
    regime_emoji = "🟢" if "green" in regime else ("🔴" if "red" in regime else "⚪")

    lines = [
        f"  {regime_emoji} {_escape_md(desc)}",
        f"  📊 Зелёных: {growing}/{total} ({pct_green}%) | Красных: {falling} ({pct_red}%)",
        f"  📈 Среднее: {avg:+.2f}%",
    ]

    # Top gainers
    gainers = meta.get("top_gainers", [])
    if gainers:
        lines.append("  🟢 <b>Топ гейнеры:</b>")
        for g in gainers:
            s = g.get("symbol", "?").split("/")[0]
            ch = g.get("change_pct", 0)
            lines.append(f"     {s} {ch:+.2f}%")

    # Top losers
    losers = meta.get("top_losers", [])
    if losers:
        lines.append("  🔴 <b>Топ лузеры:</b>")
        for g in losers:
            s = g.get("symbol", "?").split("/")[0]
            ch = g.get("change_pct", 0)
            lines.append(f"     {s} {ch:+.2f}%")

    return lines


# ── HeatMap ──

@register_formatter("heatmap")
def _fmt_heatmap(sig: SignalResult) -> list[str]:
    meta = sig.meta
    lines = []

    triggers = meta.get("triggers", [])
    if triggers:
        lines.append(f"  🔥 <b>Триггеры:</b> {', '.join(triggers)}")

    vol = meta.get("top_volume", [])
    if vol:
        lines.append("  📊 <b>Топ объём:</b>")
        for v in vol:
            vol_fmt = _fmt_volume(v.get("volume_usdt", 0))
            ch = v.get("change_pct", 0)
            lines.append(f"    {v['symbol']} {vol_fmt} ({ch:+.2f}%)")

    tm = meta.get("top_momentum", [])
    if tm:
        lines.append("  🟢 <b>Моментум ↑:</b>")
        for v in tm:
            lines.append(f"    {v['symbol']} {v['change_pct']:+.2f}%")

    bm = meta.get("bottom_momentum", [])
    if bm:
        lines.append("  🔴 <b>Моментум ↓:</b>")
        for v in bm:
            lines.append(f"    {v['symbol']} {v['change_pct']:+.2f}%")

    liq_s = meta.get("top_liq_sell", [])
    if liq_s:
        lines.append("  💥 <b>Ликвидации SELL (5m):</b>")
        for v in liq_s:
            liq_fmt = _fmt_volume(v.get("notional_usdt", 0))
            lines.append(f"    {v['symbol']} x{v['count']} {liq_fmt}")

    liq_b = meta.get("top_liq_buy", [])
    if liq_b:
        lines.append("  💚 <b>Ликвидации BUY (5m):</b>")
        for v in liq_b:
            liq_fmt = _fmt_volume(v.get("notional_usdt", 0))
            lines.append(f"    {v['symbol']} x{v['count']} {liq_fmt}")

    liq_total = meta.get("liq_total_notional", 0)
    if liq_total:
        lines.append(f"  💰 <b>Всего ликвидаций:</b> {_fmt_volume(liq_total)}")

    return lines


# ── Rotation ──

@register_formatter("rotation")
def _fmt_rotation(sig: SignalResult) -> list[str]:
    meta = sig.meta
    desc = meta.get("description", "")
    prev = meta.get("previous_leader", "?")
    curr = meta.get("current_leader", "?")
    direction = meta.get("direction", "neutral")
    strength = meta.get("strength", "weak")
    rankings = meta.get("sector_rankings", [])

    direction_emoji = "🟢" if direction == "risk_on" else ("🔴" if direction == "risk_off" else "⚪")
    strength_label = {"strong": "💪 сильная", "moderate": "📊 умеренная", "weak": "🔹 слабая"}.get(strength, "")

    lines = [
        f"  {direction_emoji} {_escape_md(desc)}",
        f"  Направление: {direction.upper()} | {strength_label}",
        f"  {prev} → {curr}",
    ]

    if rankings:
        lines.append("  📊 <b>Ранжинг секторов:</b>")
        for i, (s, rs) in enumerate(rankings, 1):
            em = "🟢" if rs > 0 else ("🔴" if rs < 0 else "⚪")
            lines.append(f"    {i}. {s} {rs:+.2f} ({em})")

    return lines


# ── Liquidity Zone ──

@register_formatter("liquidity_zone")
def _fmt_liquidity_zone(sig: SignalResult) -> list[str]:
    meta = sig.meta
    price = meta.get("current_price", 0)
    support = meta.get("nearest_support", 0)
    resistance = meta.get("nearest_resistance", 0)
    zones = meta.get("nearby_zones", [])
    desc = meta.get("description", "")

    lines = [f"  📍 {_escape_md(desc)}"]
    if price:
        lines.append(f"  💰 Цена: {price:.4f}")
    if support:
        lines.append(f"  🟢 Поддержка: {support:.4f} ({(price - support)/max(price,1)*100:+.2f}%)")
    if resistance:
        lines.append(f"  🔴 Сопротивление: {resistance:.4f} ({(resistance - price)/max(price,1)*100:+.2f}%)")

    if zones:
        lines.append("  📊 <b>Ближайшие зоны:</b>")
        for z in zones:
            zt = z.get("zone_type", "?")
            zp = z.get("price", 0)
            zd = z.get("distance_pct", 0)
            zs = z.get("strength", 0)
            icon = "🟢" if zt in ("support", "wall_bid") else "🔴"
            lines.append(f"    {icon} {zt} @ {zp:.4f} ({zd:+.2f}%, сила {zs:.0f})")

    return lines


# ── Trend Strength ──

@register_formatter("trend_strength")
def _fmt_trend_strength(sig: SignalResult) -> list[str]:
    meta = sig.meta
    direction = meta.get("trend_direction", "sideways")
    desc = meta.get("description", "")
    price = meta.get("price", 0)
    ema9 = meta.get("ema9", 0)
    ema26 = meta.get("ema26", 0)
    hh_hl = meta.get("hh_hl", "neutral")
    signal_v = meta.get("signal_value", "neutral")

    dir_emoji = {
        "strong_up": "🚀", "weak_up": "📈",
        "sideways": "➡️",
        "weak_down": "📉", "strong_down": "💀",
    }.get(direction, "⚪")
    sig_emoji = {
        "strong_buy": "🟢🟢", "buy": "🟢",
        "neutral": "⚪",
        "sell": "🔴", "strong_sell": "🔴🔴",
    }.get(signal_v, "⚪")

    lines = [
        f"  {dir_emoji} {_escape_md(desc)}",
        f"  Сигнал: {sig_emoji} {signal_v.upper()}",
    ]
    if price:
        lines.append(f"  💰 {price:.4f} | EMA9 {ema9:.4f} | EMA26 {ema26:.4f}")
    lines.append(f"  🏔 Структура: {hh_hl} | Наклон: {meta.get('slope_5m', 0):+.6f}")

    return lines


# ── Main formatter ──

def signal_to_text(sig: SignalResult) -> str:
    """Форматировать один сигнал через реестр форматтеров."""
    priority = _priority_label(sig.score)
    emoji = "🟢" if sig.direction == "buy" else ("🔴" if sig.direction == "sell" else "⚪")
    meta = sig.meta

    lines = [
        f"{emoji} {sig.symbol}",
        f"  {priority} | {_escape_md(sig.signal_name)} | Score: **{sig.score:.0f}/100**",
    ]

    if sig.direction != "neutral":
        lines.append(f"  Direction: **{sig.direction.upper()}**")

    # Форматтер по типу сигнала
    formatter = _SIGNAL_FORMATTERS.get(sig.signal_name)
    if formatter:
        try:
            lines.extend(formatter(sig))
        except Exception:
            logger.exception("[fmt] formatter error for %s", sig.signal_name)

    # Explainable AI — короткое объяснение
    try:
        from core.explainable_ai import explain
        ex = explain(sig)
        if ex.body:
            lines.append(f"\n💡 {ex.body}")
    except Exception:
        pass

    return "\n".join(lines)


def signal_to_simple_text(sig: SignalResult) -> str:
    """Краткая строка для ленты сигналов."""
    emoji = "🟢" if sig.direction == "buy" else ("🔴" if sig.direction == "sell" else "⚪")
    return f"{emoji} {sig.symbol:12s} | {sig.signal_name:15s} | {sig.score:.0f}/100"


# ── TelegramNotifier ───────────────────────────────────────────────

class TelegramNotifier:
    """Отправляет сигналы в Telegram. aiogram или прямой HTTP API."""

    def __init__(self, token: str, chat_id: str, use_aiogram: bool = True):
        self.token = token
        self.chat_id = chat_id
        self._bot: Bot | None = None
        self._dp: Dispatcher | None = None
        self._use_aiogram = use_aiogram and HAS_AIOGRAM
        self._signal_listeners: list[callable] = []

    def register_signal_listener(self, fn: callable):
        """Подписать слушателя на каждый отправленный сигнал.
        Вызывается для каждого SignalResult после отправки в Telegram.
        """
        self._signal_listeners.append(fn)

    async def _notify_listeners(self, sig):
        """Оповестить всех слушателей о сигнале."""
        for fn in self._signal_listeners:
            try:
                if asyncio.iscoroutinefunction(fn):
                    await fn(sig)
                else:
                    fn(sig)
            except Exception:
                logger.exception("[notifier] signal listener error: %s", fn)

    @property
    def _proxy(self) -> str | None:
        return os.getenv("CS_TG_PROXY") or None

    async def start(self):
        if self._use_aiogram:
            from aiogram.client.session.aiohttp import AiohttpSession

            proxy = self._proxy
            kwargs = {}
            if proxy:
                kwargs["session"] = AiohttpSession(proxy=proxy)
                logger.info("Telegram notifier started (aiogram + SOCKS5)")
            else:
                logger.info("Telegram notifier started (aiogram, direct)")
            self._bot = Bot(token=self.token, **kwargs)
            self._dp = Dispatcher()

    async def attach_router(self, router):
        """Подключить Router с командами управления."""
        if self._dp:
            self._dp.include_router(router)
            logger.info("Router attached to Telegram notifier")

    async def start_polling(self):
        """Запустить long-polling (фоновая задача)."""
        if self._bot and self._dp:
            logger.info("Starting Telegram polling...")
            asyncio.create_task(self._dp.start_polling(self._bot))
            logger.info("Telegram polling task created")

    async def stop(self):
        if self._bot:
            await self._bot.session.close()

    async def send_signal(self, sig: SignalResult):
        """Отправить один сигнал."""
        text = signal_to_text(sig)
        await self._send(text)
        await self._notify_listeners(sig)

    async def send_batch(self, signals: list[SignalResult]):
        """Отправить группу сигналов."""
        if not signals:
            return
        text = "\n\n".join(signal_to_text(s) for s in signals)
        # Telegram limit 4096 chars
        if len(text) > 4000:
            text = text[:4000] + "\n\n..."
        await self._send(text)
        for sig in signals:
            await self._notify_listeners(sig)

    async def send_signal_feed(self, signals: list[SignalResult], batch_size: int = 10):
        """Отправить сигналы в виде ленты (каждый сигнал отдельным сообщением)."""
        for sig in signals[:batch_size]:
            await self.send_signal(sig)
            await asyncio.sleep(0.3)  # rate limit ~3 msg/sec

    async def send_text(self, text: str):
        """Отправить произвольный текст как сообщение (для аналитики, статистики)."""
        await self._send(text)

    async def _send(self, text: str):
        """Отправить текст с retry/backoff."""
        if not self.token or not self.chat_id:
            logger.warning("Telegram not configured — skip send")
            return

        await _send_with_retry(self._send_impl, text)

    async def _send_impl(self, text: str):
        """Одна попытка отправки (aiogram → HTTP fallback)."""
        if self._use_aiogram and self._bot:
            try:
                await self._bot.send_message(
                    chat_id=self.chat_id, text=text, parse_mode="Markdown"
                )
                logger.info("[tg] sent %d chars to %s", len(text), self.chat_id)
                return
            except Exception as exc:
                err_msg = str(exc)
                if "can't parse entities" in err_msg:
                    logger.warning(
                        "[tg] markdown parse error, retrying as plain text"
                    )
                    await _send_with_retry(
                        self._bot.send_message,
                        chat_id=self.chat_id,
                        text=text,
                        parse_mode=None,
                    )
                    logger.info("[tg] sent %d chars (plain) to %s", len(text), self.chat_id)
                    return
                # 429 — пробрасываем для retry
                if "429" in err_msg or "retry_after" in err_msg.lower():
                    raise
                logger.exception("aiogram send failed, fallback to HTTP+SOCKS5")
            # fallback regardless
        # Прямой HTTP API с SOCKS5
        await self._send_http(text)

    async def _send_http(self, text: str):
        """Прямой HTTP API с поддержкой Retry-After."""
        import aiohttp

        proxy = self._proxy
        kwargs = {}
        if proxy:
            from aiohttp_socks import ProxyConnector
            kwargs["connector"] = ProxyConnector.from_url(proxy)

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        async with aiohttp.ClientSession(**kwargs) as session:
            async with session.post(
                url,
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                },
            ) as resp:
                if resp.status == 429:
                    # Retry-After
                    retry_after_s = resp.headers.get("Retry-After", "5")
                    try:
                        wait = float(retry_after_s)
                    except ValueError:
                        wait = 5.0
                    raise Exception(
                        f"Telegram 429: retry_after={wait}s "
                        f"{await resp.text()}"
                    )
                if not resp.ok:
                    body = await resp.json()
                    if body.get("description", "").startswith(
                        "Bad Request: can't parse entities"
                    ):
                        logger.warning(
                            "[tg] HTTP markdown parse error, retrying plain"
                        )
                        async with session.post(url, json={
                            "chat_id": self.chat_id,
                            "text": text,
                        }) as resp2:
                            if resp2.status == 429:
                                retry_after_s = resp2.headers.get("Retry-After", "5")
                                raise Exception(
                                    f"Telegram 429 plain: retry_after={retry_after_s}s"
                                )
                            if not resp2.ok:
                                logger.error(
                                    "Telegram API error (plain): %s",
                                    await resp2.text(),
                                )
                        return
                    logger.error("Telegram API error: %s", await resp.text())


# singleton
_notifier: TelegramNotifier | None = None


def get_notifier() -> TelegramNotifier | None:
    return _notifier


def setup_notifier(token: str, chat_id: str) -> TelegramNotifier:
    global _notifier
    _notifier = TelegramNotifier(token=token, chat_id=chat_id)
    return _notifier
