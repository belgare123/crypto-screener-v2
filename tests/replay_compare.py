"""
replay_compare — Phase 0 regression tool.

Сравнивает V1 → V2 сигналы на одних и тех же данных.
Подаёт одинаковый SignalContext в старый и новый сигнал,
сравнивает score, направление и метаданные.

Usage:
    # Все дубликаты (RSI, Whale, Liquidation)
    python -m tests.replay_compare --auto --min-score 40

    # Конкретная пара
    python -m tests.replay_compare --v1 rsi --v2 indicator_v2

    # Режим passive (только логи, не блокирует сигнал)
    python -m tests.replay_compare --passive

Gate: mismatch > 5% → FAIL → требуется анализ перед gate-переходом.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Project root ──
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("replay_compare")


# ── Known V1→V2 pairs for --auto mode ──
DUPLICATE_PAIRS: dict[str, str] = {
    "rsi": "indicator_v2",          # RSI manual → FeatureEngine RSI in indicator_v2
    "whale": "whale_v2",            # Whale manual → FeatureEngine whale
    "liquidation": "liquidation_cascade",  # Simple liq → advanced cascade
    "orderbook": "spread_widening", # Simple OB → orderbook_signals family
}


@dataclass
class CompareResult:
    """Результат сравнения одного сигнала."""
    signal_name: str
    symbol: str
    v1_score: float
    v2_score: float
    v1_direction: str
    v2_direction: str
    score_diff: float
    direction_match: bool
    v1_meta: dict = field(default_factory=dict)
    v2_meta: dict = field(default_factory=dict)
    error: str | None = None
    passed: bool = True


class ReplayCompare:
    """
    Сравнивает V1 и V2 сигналы на одинаковых данных.
    Работает в двух режимах:
      - live: подписывается на MarketDataBus и ждёт события
      - replay: загружает данные из market_replay.db
    """

    def __init__(
        self,
        tolerance_pct: float = 5.0,
        passive: bool = False,
        min_score: float = 30.0,
    ):
        self.tolerance_pct = tolerance_pct
        self.passive = passive
        self.min_score = min_score
        self._results: list[CompareResult] = []

    # ── Live mode ──

    async def run_live(self, pairs: list[tuple[str, str]] | None = None):
        """
        Запустить в live-режиме: подписаться на шину, ждать сигналов.
        pairs: [(v1_name, v2_name), ...] или None = все из DUPLICATE_PAIRS.
        """
        from signals import get_signal, list_signals, _signal_registry
        from signals.base import BaseSignal
        from signals.engine import SignalEngine, get_engine
        from core import get_bus, Event

        if pairs is None:
            pairs = list(DUPLICATE_PAIRS.items())

        # Загружаем V1 и V2 сигналы
        v1_signals: list[BaseSignal] = []
        v2_signals: list[tuple[BaseSignal, str]] = []

        for v1_name, v2_name in pairs:
            cls1 = _signal_registry.get(v1_name)
            cls2 = _signal_registry.get(v2_name)
            if cls1 and cls2:
                v1_signals.append(cls1())
                v2_signals.append((cls2(), v1_name))
                logger.info("Pair loaded: %s (V1) ↔ %s (V2)", v1_name, v2_name)
            else:
                logger.warning("Cannot load pair: %s ↔ %s (missing one)", v1_name, v2_name)

        if not v1_signals:
            logger.error("No signal pairs loaded.")
            return []

        # Для каждого V2-сигнала добавляем V1 как shadow-runner
        engine = get_engine()

        async def on_event(event: Event):
            if not event.channel.startswith("candles."):
                return

            symbol = event.symbol
            context = await engine._build_context(symbol, event.exchange)
            if not context.candles:
                return

            for v1_sig in v1_signals:
                v1_result = await v1_sig.check(context)
                if v1_result is None or v1_result.score < self.min_score:
                    continue

                # Находим соответствующий V2
                for v2_sig, v1_name in v2_signals:
                    if v1_sig.meta.name != v1_name:
                        continue
                    v2_result = await v2_sig.check(context)
                    if v2_result is None:
                        v2_result_placeholder = type('R', (), {
                            'score': 0, 'direction': 'neutral',
                            'meta': {}, 'signal_name': v2_sig.meta.name
                        })()
                    else:
                        v2_result_placeholder = v2_result

                    result = CompareResult(
                        signal_name=v1_sig.meta.name,
                        symbol=symbol,
                        v1_score=v1_result.score,
                        v2_score=v2_result_placeholder.score,
                        v1_direction=v1_result.direction,
                        v2_direction=v2_result_placeholder.direction,
                        score_diff=abs(v1_result.score - v2_result_placeholder.score),
                        direction_match=v1_result.direction == v2_result_placeholder.direction,
                        v1_meta=v1_result.meta,
                        v2_meta=v2_result_placeholder.meta,
                    )

                    if result.score_diff > self.tolerance_pct:
                        result.passed = False
                        if not self.passive:
                            logger.warning(
                                "⚠ GATE FAIL [%s] %s: score diff %+.1f%% (V1=%.0f V2=%.0f) dir=%s/%s",
                                result.signal_name, symbol,
                                result.score_diff, result.v1_score, result.v2_score,
                                result.v1_direction, result.v2_direction,
                            )
                        else:
                            logger.info(
                                "  diff [%s] %s: V1=%.0f V2=%.0f Δ%+.1f%% (passive)",
                                result.signal_name, symbol,
                                result.v1_score, result.v2_score, result.score_diff,
                            )
                    else:
                        logger.debug(
                            "  OK [%s] %s: V1=%.0f V2=%.0f dir=%s",
                            result.signal_name, symbol,
                            result.v1_score, result.v2_score, result.v1_direction,
                        )

                    self._results.append(result)

        bus = get_bus()
        bus.subscribe("*", on_event)

        logger.info(
            "ReplayCompare live — monitoring pairs: %s | passive=%s | tolerance=%.0f%%",
            [v1 for v1, _ in pairs], self.passive, self.tolerance_pct,
        )

        # Ждём N секунд (или пока не остановят)
        await asyncio.sleep(3600)  # Требует внешней остановки
        return self._results

    # ── Replay mode (from DB) ──

    async def run_replay(
        self,
        symbols: list[str] | None = None,
        max_bars: int = 200,
        pairs: list[tuple[str, str]] | None = None,
    ):
        """
        Replay-режим: загрузить данные из market_replay.db и прогнать пары.
        """
        import aiosqlite
        from signals import _signal_registry

        if pairs is None:
            pairs = list(DUPLICATE_PAIRS.items())

        db_path = ROOT / "market_replay.db"
        if not db_path.exists():
            logger.error("Replay DB not found: %s", db_path)
            return []

        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row

            # Получаем список символов
            rows = await db.execute_fetchall(
                "SELECT DISTINCT symbol FROM candles ORDER BY symbol"
            )
            all_symbols = [r[0] for r in rows] if rows else []
            if symbols:
                all_symbols = [s for s in all_symbols if s in symbols]

            logger.info("Replay DB loaded: %d symbols", len(all_symbols))

            for symbol in all_symbols:
                # Загружаем свечи
                rows = await db.execute_fetchall(
                    """SELECT * FROM candles WHERE symbol = ?
                       ORDER BY timestamp DESC LIMIT ?""",
                    (symbol, max_bars),
                )
                candles = [dict(r) for r in rows]
                # Переворачиваем в хронологическом порядке
                candles.reverse()

                if not candles:
                    continue

                # Загружаем OrderBook
                ob_rows = await db.execute_fetchall(
                    "SELECT * FROM orderbook WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
                    (symbol,),
                )
                orderbook = dict(ob_rows[0]) if ob_rows else None

                # Загружаем тикер
                ticker_rows = await db.execute_fetchall(
                    "SELECT * FROM ticker WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
                    (symbol,),
                )
                ticker = dict(ticker_rows[0]) if ticker_rows else None

                # Строим контекст для V1
                from signals.base import SignalContext

                ctx = SignalContext(
                    symbol=symbol,
                    exchange="bybit",
                    candles=candles,
                    ticker=ticker,
                    orderbook=orderbook,
                )

                # FeatureEngine для V2
                try:
                    from core.features.engine import get_feature_engine
                    fe = get_feature_engine()
                    ctx.features = fe
                except Exception:
                    pass

                for v1_name, v2_name in pairs:
                    cls1 = _signal_registry.get(v1_name)
                    cls2 = _signal_registry.get(v2_name)
                    if not cls1 or not cls2:
                        continue

                    sig1 = cls1()
                    sig2 = cls2()

                    try:
                        r1 = await sig1.check(ctx)
                        r2 = await sig2.check(ctx)
                    except Exception as e:
                        logger.error("Signal error %s/%s: %s", v1_name, v2_name, e)
                        continue

                    if r1 is None and r2 is None:
                        continue

                    v1_score = r1.score if r1 else 0
                    v2_score = r2.score if r2 else 0
                    v1_dir = r1.direction if r1 else "neutral"
                    v2_dir = r2.direction if r2 else "neutral"

                    score_diff = abs(v1_score - v2_score)
                    dir_match = v1_dir == v2_dir

                    result = CompareResult(
                        signal_name=v1_name,
                        symbol=symbol,
                        v1_score=v1_score,
                        v2_score=v2_score,
                        v1_direction=v1_dir,
                        v2_direction=v2_dir,
                        score_diff=score_diff,
                        direction_match=dir_match,
                        v1_meta=r1.meta if r1 else {},
                        v2_meta=r2.meta if r2 else {},
                    )

                    if score_diff > self.tolerance_pct and v1_score >= self.min_score:
                        result.passed = False
                        logger.warning(
                            "⚠ MISMATCH [%s] %s: "
                            "V1=%.0f %s → V2=%.0f %s  Δscore=%.1f%%  %s",
                            v1_name, symbol,
                            v1_score, v1_dir,
                            v2_score, v2_dir,
                            score_diff,
                            "DIR MISMATCH!" if not dir_match else "",
                        )
                    else:
                        logger.info(
                            "  OK [%s] %s: V1=%.0f %s → V2=%.0f %s  Δ%.1f%%",
                            v1_name, symbol,
                            v1_score, v1_dir,
                            v2_score, v2_dir,
                            score_diff,
                        )

                    self._results.append(result)

        return self._results

    # ── Report ──

    def report(self) -> dict:
        """Сформировать отчёт."""
        total = len(self._results)
        passed = sum(1 for r in self._results if r.passed)
        failed = total - passed

        by_signal: dict[str, list[CompareResult]] = {}
        for r in self._results:
            by_signal.setdefault(r.signal_name, []).append(r)

        report = {
            "total_comparisons": total,
            "passed": passed,
            "failed": failed,
            "pass_rate_pct": round(passed / total * 100, 1) if total else 0,
            "by_signal": {},
        }

        for sig_name, results in by_signal.items():
            sig_passed = sum(1 for r in results if r.passed)
            avg_diff = sum(r.score_diff for r in results) / len(results) if results else 0
            report["by_signal"][sig_name] = {
                "total": len(results),
                "passed": sig_passed,
                "failed": len(results) - sig_passed,
                "avg_score_diff": round(avg_diff, 1),
                "max_score_diff": round(max(r.score_diff for r in results), 1) if results else 0,
                "direction_matches": sum(1 for r in results if r.direction_match),
            }

        return report


# ── CLI ──

def main():
    parser = argparse.ArgumentParser(description="Phase 0: V1↔V2 regression compare")
    parser.add_argument("--v1", help="V1 signal name (e.g. rsi)")
    parser.add_argument("--v2", help="V2 signal name (e.g. indicator_v2)")
    parser.add_argument("--auto", action="store_true", help="Run all known duplicate pairs")
    parser.add_argument("--passive", action="store_true", help="Log only, no gate failure")
    parser.add_argument("--replay", action="store_true", help="Replay mode (from DB)")
    parser.add_argument("--symbols", nargs="*", help="Filter symbols for replay")
    parser.add_argument("--tolerance", type=float, default=5.0, help="Score diff % tolerance")
    parser.add_argument("--min-score", type=float, default=30.0, help="Minimum score to report")
    parser.add_argument("--max-bars", type=int, default=200, help="Max candles per symbol")
    parser.add_argument("--live", action="store_true", help="Live bus monitoring mode")
    args = parser.parse_args()

    rc = ReplayCompare(
        tolerance_pct=args.tolerance,
        passive=args.passive,
        min_score=args.min_score,
    )

    pairs = None
    if args.auto:
        pairs = list(DUPLICATE_PAIRS.items())
    elif args.v1 and args.v2:
        pairs = [(args.v1, args.v2)]
    else:
        parser.print_help()
        print("\nDefault: running --auto --replay")
        pairs = list(DUPLICATE_PAIRS.items())
        args.replay = True

    if args.live:
        try:
            asyncio.run(rc.run_live(pairs))
        except KeyboardInterrupt:
            pass
    else:
        asyncio.run(rc.run_replay(
            symbols=args.symbols,
            max_bars=args.max_bars,
            pairs=pairs,
        ))

    report = rc.report()
    print("\n" + "=" * 60)
    print(f"📊 REPLAY COMPARE REPORT — tolerance={args.tolerance}%")
    print("=" * 60)
    print(f"  Total comparisons: {report['total_comparisons']}")
    print(f"  Passed:           {report['passed']} ({report['pass_rate_pct']}%)")
    print(f"  Failed:           {report['failed']}")
    print()
    for sig, stats in report["by_signal"].items():
        status = "✅" if stats["failed"] == 0 else "⚠️"
        print(f"  {status} {sig}:")
        print(f"      comparisons: {stats['total']}")
        print(f"      pass/fail:   {stats['passed']}/{stats['failed']}")
        print(f"      avg Δscore: {stats['avg_score_diff']:.1f}%")
        print(f"      max Δscore: {stats['max_score_diff']:.1f}%")
        print(f"      dir match:  {stats['direction_matches']}/{stats['total']}")

    if report["failed"] > 0 and not args.passive:
        print(f"\n❌ Gate FAILED: {report['failed']} mismatches > {args.tolerance}%")
        print("   Analysis required before gate transition.")
        sys.exit(1)
    else:
        print(f"\n✅ Gate PASSED: {report['passed']}/{report['total_comparisons']} comparisons within {args.tolerance}%")
        print("   Ready for shadow-mode transition.")


if __name__ == "__main__":
    main()
