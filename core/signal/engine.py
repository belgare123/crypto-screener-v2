"""
SignalEngine — связующее звено между pipeline и Telegram.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Callable

from core import SignalResult
from core.consensus.models import ConsensusResult, SignalDirection, SignalVote
from core.risk.models import RiskVerdict, RiskContext, RiskResult
from core.ome import OME, OrderSide, OrderType

logger = logging.getLogger(__name__)


class SignalEngine:
    """Главный pipeline для превращения консенсуса в сигнал + отправку.

    Flow:
    consensus → risk → sizer → OME → cooldown → SignalResult → Telegram
    """

    def __init__(self, risk_engine=None, ome: OME | None = None,
                 notifier=None, metrics_registry=None,
                 cooldown_default: int = 1800,
                 shadow: bool = True):
        """
        Args:
            risk_engine: RiskEngine для пре-трейд фильтрации
            ome: OME для расчёта qty + SL/TP (может быть None)
            notifier: TelegramNotifier для отправки
            metrics_registry: MetricsRegistry для логов
            cooldown_default: антиспам в секундах
            shadow: если True — не отправляет реально
        """
        self.risk_engine = risk_engine
        self.ome = ome
        self.notifier = notifier
        self.metrics = metrics_registry
        self.cooldown_default = cooldown_default
        self.shadow = shadow

        self._lock = threading.Lock()
        # cooldown key: f"{symbol}:{signal_name}" → last_sent_ts
        self._cooldowns: dict[str, float] = {}

    # ── Core entry ──

    async def process_consensus(self, consensus: ConsensusResult,
                                atr: float, price: float,
                                signal_name: str = "consensus",
                                exchange: str = "bybit") -> SignalResult | None:
        """Обработать ConsensusResult → SignalResult (+ опционально отправить).

        Returns:
            SignalResult если сигнал прошёл все проверки, None если заблокирован.
        """
        symbol = consensus.symbol
        direction = consensus.direction
        score = consensus.score
        confidence = consensus.confidence

        if direction == SignalDirection.NEUTRAL or score < 1.0:
            return None

        direction_str = direction.value

        # 1. Cooldown check
        cd_key = f"{symbol}:{signal_name}"
        if self._check_cooldown(cd_key):
            if self.metrics:
                self.metrics.inc("signal_cooldown_blocked",
                                 labels={"symbol": symbol, "signal": signal_name})
            logger.debug("[signal] Cooldown active for %s", cd_key)
            return None

        # 2. Risk check
        if self.risk_engine:
            spread_bps = next((v.extra.get("spread_bps", 0.0) for v in consensus.votes if v.extra), 0.0)
            volume_24h = next((v.extra.get("volume_24h", 0) for v in consensus.votes if v.extra), 0)
            ctx = RiskContext(
                symbol=symbol,
                side=direction_str,
                entry_price=price,
                spread_bps=spread_bps,
                atr_14_pct_bps=atr * 100 if atr else 0,
                volume_24h_usd=volume_24h,
            )
            risk_result = self.risk_engine.evaluate(ctx)
            if risk_result.verdict == RiskVerdict.BLOCK:
                if self.metrics:
                    self.metrics.inc("signal_blocked_by_risk",
                                     labels={"symbol": symbol, "reason": risk_result.reason.value})
                logger.info("[signal] BLOCKED %s %s — %s", symbol, direction_str, risk_result.reason.value)
                return None

        # 3. OME (qty + SL/TP)
        ome_result = None
        if self.ome:
            ome_result = self.ome.execute_signal(
                symbol=symbol,
                side=direction_str,
                price=price,
                atr=atr,
            )

        # 4. Build SignalResult
        meta = {
            "signal_name": signal_name,
            "score": round(score, 1),
            "confidence": round(confidence, 2),
            "atr": round(atr, 2),
            "price": round(price, 2),
            "votes": consensus.total_votes,
            "buy_ratio": round(consensus.buy_ratio, 2),
        }
        if ome_result:
            meta.update({
                "qty": ome_result["sizer"]["qty"],
                "stop_loss": ome_result["risk"]["stop_loss"],
                "take_profit": ome_result["risk"]["take_profit"],
                "rrr": ome_result["risk"]["risk_reward_ratio"],
            })

        sig = SignalResult(
            signal_name=signal_name,
            symbol=symbol,
            exchange=exchange,
            score=score,
            direction=direction_str,
            meta=meta,
            ts=time.time(),
            cooldown=self.cooldown_default,
        )

        # 5. Set cooldown
        self._set_cooldown(cd_key)

        # 6. Metrics
        if self.metrics:
            self.metrics.inc("signals_total", labels={"signal": signal_name, "direction": direction_str})

        # 7. Send
        if self.notifier and not self.shadow:
            await self.notifier.send_signal(sig)
            logger.info("[signal] SENT %s %s score=%.1f", symbol, direction_str, score)
        else:
            logger.info("[signal] SHADOW %s %s score=%.1f qty=%s",
                       symbol, direction_str, score,
                       ome_result["sizer"]["qty"] if ome_result else "N/A")

        return sig

    async def process_batch(self, consensus_list: list[ConsensusResult],
                            symbol_data: dict[str, dict],
                            signal_name: str = "consensus") -> list[SignalResult]:
        """Обработать список ConsensusResult (один тик)."""
        results: list[SignalResult] = []
        for c in consensus_list:
            data = symbol_data.get(c.symbol, {})
            sig = await self.process_consensus(
                consensus=c,
                atr=data.get("atr", 0),
                price=data.get("price", 0),
                signal_name=signal_name,
            )
            if sig:
                results.append(sig)
        return results

    # ── Cooldown ──

    def _check_cooldown(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            last = self._cooldowns.get(key, 0)
            return (now - last) < self.cooldown_default

    def _set_cooldown(self, key: str):
        with self._lock:
            self._cooldowns[key] = time.time()

    def clear_cooldowns(self):
        with self._lock:
            self._cooldowns.clear()


# Singleton
_engine: SignalEngine | None = None


def get_signal_engine(risk_engine=None, ome: OME | None = None,
                      notifier=None, metrics_registry=None,
                      shadow: bool = True) -> SignalEngine:
    global _engine
    if _engine is None:
        _engine = SignalEngine(
            risk_engine=risk_engine,
            ome=ome,
            notifier=notifier,
            metrics_registry=metrics_registry,
            shadow=shadow,
        )
    return _engine


def reset_signal_engine():
    global _engine
    _engine = None
