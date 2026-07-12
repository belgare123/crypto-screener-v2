"""
Analytics pipeline: запись сигналов в БД + фоновый расчёт win/loss.

Компоненты:
  - SignalRecorder: слушает сигналы из Dispatcher и пишет в SignalDB
  - WinRateChecker: раз в 5 мин проверяет старые сигналы, оценивает win/loss

Интеграция:
  1. Dispatcher вызывает recorder.on_signal(sig) после отправки
  2. WinRateChecker стартует как asyncio-таск в run.py
"""
from __future__ import annotations

import asyncio
import logging
import time

from core import SignalResult
from storage.db import SignalDB

logger = logging.getLogger(__name__)

# Единственный экземпляр DB (singleton для всего процесса)
_db: SignalDB | None = None


def get_db() -> SignalDB:
    global _db
    if _db is None:
        _db = SignalDB()
    return _db


# ────────────────────────────────────────────────────────────
#  SignalRecorder
# ────────────────────────────────────────────────────────────


class SignalRecorder:
    """Подключается к Dispatcher, пишет каждый сигнал в БД."""

    def __init__(self, db: SignalDB | None = None, ticker_getter=None):
        """
        ticker_getter: async callable(symbol) → float | None
        Если не передан, цена берётся из sig.meta (старое поведение).
        """
        self.db = db or get_db()
        self._ticker_getter = ticker_getter
        self._started = False

    async def start(self):
        await self.db.init()
        self._started = True
        logger.info("SignalRecorder started")

    async def on_signal(self, sig: SignalResult):
        """Вызывается Dispatcher'ом для каждого отправленного сигнала."""
        if not self._started:
            return
        try:
            # 1. Цена из meta сигнала
            price = sig.meta.get("price") if sig.meta else None
            # 2. Если нет — пробуем получить из ticker_getter (если передан)
            if price is None and self._ticker_getter:
                try:
                    price = await self._ticker_getter(sig.symbol)
                except Exception:
                    pass
            if price is not None:
                price = float(price)
            await self.db.record(
                signal_name=sig.signal_name,
                symbol=sig.symbol,
                direction=sig.direction,
                score=sig.score,
                price=price,
                exchange=sig.exchange,
                meta=sig.meta,
            )
        except Exception:
            logger.exception("SignalRecorder failed on %s/%s", sig.symbol, sig.signal_name)

    async def stop(self):
        await self.db.close()
        self._started = False


# ────────────────────────────────────────────────────────────
#  WinRateChecker
# ────────────────────────────────────────────────────────────


class WinRateChecker:
    """
    Раз в CHECK_INTERVAL проверяет сигналы старше MIN_AGE.
    Сверяет текущую цену с ценой входа:
      - buy:  exit > entry → win
      - sell: exit < entry → win
    """

    CHECK_INTERVAL = 300   # 5 минут
    MIN_AGE = 30           # оцениваем сигналы старше 30 минут
    MIN_PNL_THRESHOLD = 0.3  # минимальное движение цены в % для засчитывания результата

    def __init__(self, db: SignalDB | None = None, ticker_getter=None):
        """
        ticker_getter: async callable(symbol) → current_price | None
        Если не передан, цена берётся из последнего тикера символа.
        """
        self.db = db or get_db()
        self._ticker_getter = ticker_getter
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self):
        if self._ticker_getter is None:
            self._ticker_getter = self._default_ticker_getter
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("WinRateChecker started (every %ds, min age %dm)", self.CHECK_INTERVAL, self.MIN_AGE)

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _default_ticker_getter(self, symbol: str) -> float | None:
        """Заглушка — будет заменена в run.py на getter из TickerStore."""
        return None

    async def _loop(self):
        while self._running:
            try:
                await asyncio.sleep(self.CHECK_INTERVAL)
                await self._check_pending()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("WinRateChecker: error in loop")

    async def _check_pending(self):
        pending = await self.db.get_pending(min_age_minutes=self.MIN_AGE)
        if not pending:
            return

        logger.debug("WinRateChecker: evaluating %d signals", len(pending))
        for sig in pending:
            try:
                await self._evaluate_one(sig)
            except Exception:
                logger.exception("WinRateChecker: failed to evaluate signal %s", sig["id"])

    async def _evaluate_one(self, sig: dict):
        current_price = await self._ticker_getter(sig["symbol"])
        if current_price is None or current_price <= 0:
            return  # нет данных — пропускаем

        entry_price = sig["price"]
        if entry_price is None or entry_price <= 0:
            return

        direction = sig["direction"]
        pnl_pct = (current_price - entry_price) / entry_price * 100

        # Если движение меньше порога — считаем нейтральным (not enough movement)
        if abs(pnl_pct) < self.MIN_PNL_THRESHOLD:
            # Для buy/sell — это невыраженное движение
            if direction in ("buy", "sell"):
                result = "loss"  # сигнал был, а движения нет — неудачно
                pnl_display = 0.0
            else:
                # Нейтральный сигнал — цена не двинулась, значит всё верно
                result = "win"
                pnl_display = 0.0
        elif direction == "buy":
            result = "win" if pnl_pct > 0 else "loss"
            pnl_display = pnl_pct
        elif direction == "sell":
            result = "win" if pnl_pct < 0 else "loss"
            pnl_display = -pnl_pct  # для sell прибыль = падение цены
        elif direction == "neutral":
            # Нейтральный сигнал = отсутствие сильного движения
            result = "win"
            pnl_display = 0.0
        else:
            # unknown direction
            result = "loss"
            pnl_display = 0.0

        await self.db.evaluate(sig["id"], current_price, result, round(pnl_display, 2))


# ────────────────────────────────────────────────────────────
#  PeriodicStatsReporter
# ────────────────────────────────────────────────────────────


class StatsReporter:
    """
    Раз в час отправляет сводку win rate в Telegram.
    Вызывается из run.py.
    """

    REPORT_INTERVAL = 3600  # 1 час

    def __init__(self, db: SignalDB, notifier=None):
        self.db = db
        self._notifier = notifier
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("StatsReporter started (every %ds)", self.REPORT_INTERVAL)

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self):
        # первый отчёт через 1 час (даём сигналам накопиться)
        await asyncio.sleep(self.REPORT_INTERVAL)
        while self._running:
            try:
                await self._send_report()
            except Exception:
                logger.exception("StatsReporter: error")
            await asyncio.sleep(self.REPORT_INTERVAL)

    async def _send_report(self):
        stats = await self.db.get_stats(days=1)
        if stats["total"] == 0:
            logger.debug("StatsReporter: no signals yet, skipping")
            return

        top = await self.db.get_top_signals(days=1, limit=10)
        lines = [
            "📊 *Статистика сигналов (24ч)*\n",
            f"Всего сигналов: {stats['total']}",
            f"✅ Выигрышей: {stats['wins']}",
            f"❌ Проигрышей: {stats['losses']}",
            f"⏳ В ожидании: {stats['pending']}",
            f"💰 Средний PnL: {stats['avg_pnl']:+.2f}%",
            f"🏆 Win Rate: {stats['wins'] * 100 // max(stats['wins'] + stats['losses'], 1)}%",
        ]

        if top:
            lines.append("\n*Топ сигналов по типам:*")
            lines.append(f"`{'Тип':16s}` `{'Всего':>6s}` `{'✓':>4s}` `{'✗':>4s}` `{'⌛':>5s}` `{'WR':>5s}` `{'PnL':>7s}`")
            for s in top:
                wr = s["wins"] * 100 // max(s["wins"] + s["losses"], 1)
                total = s["total"]
                evaled = s["wins"] + s["losses"]
                pending = total - evaled
                lines.append(
                    f"`{s['signal_name']:16s}` "
                    f"`{total:6d}` `{s['wins']:4d}` `{s['losses']:4d}` "
                    f"`{pending:5d}` `{wr:3d}%` `{s['avg_pnl']:+.2f}%`"
                )

        message = "\n".join(lines)

        if self._notifier and hasattr(self._notifier, "send_text"):
            try:
                await self._notifier.send_text(message)
            except Exception:
                logger.exception("StatsReporter: failed to send")
        else:
            logger.info("Stats report:\n%s", message)
