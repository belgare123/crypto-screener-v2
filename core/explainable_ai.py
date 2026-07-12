"""
Explainable AI (#20) — генерация человеко-читаемых объяснений сигналов.

Вместо "Score: 91" → "BTC whale detected: 3,200 BTC moved to Binance — accumulation"
Использует meta-данные сигнала и шаблоны.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from core import SignalResult

logger = logging.getLogger(__name__)


@dataclass
class Explanation:
    signal_name: str = ""
    symbol: str = ""
    score: float = 0
    direction: str = "neutral"
    headline: str = ""              # 🟢 BTC: whale accumulation (score 91)
    body: str = ""                  # Что и почему
    triggers: list[str] = field(default_factory=list)
    context: list[str] = field(default_factory=list)


def _fmt(sig: SignalResult, meta: dict | None, ticker: dict | None) -> Explanation:
    """Сгенерировать объяснение для сигнала."""
    sym = sig.symbol.split("/")[0]
    score = round(sig.score, 0)
    direction = sig.direction or "neutral"
    meta = meta or {}
    ticker = ticker or {}

    dir_icon = "🟢" if direction == "buy" else ("🔴" if direction == "sell" else "⚪")
    price = float(ticker.get("last_price", 0)) if ticker else 0
    price_str = f" @ ${price:,.2f}" if price > 0 else ""

    name = sig.signal_name
    headline = f"{dir_icon} {sym}: {name} ({score}/100){price_str}"
    body = ""
    triggers = []
    context_list = []

    price_change = meta.get("price_change_pct")
    volume_spike = meta.get("volume_ratio")
    whale_amount = meta.get("amount")
    whale_value = meta.get("value")
    wall_size = meta.get("wall_size")
    wall_dist = meta.get("distance_pct")
    oi_change = meta.get("oi_change_pct")
    taker_buy = meta.get("taker_buy_ratio")
    cvd_val = meta.get("cvd_value")
    cvd_div = meta.get("cvd_divergence")
    liq_total = meta.get("total_liquidations")
    liq_side = meta.get("side")
    cluster_id = meta.get("cluster_id")
    cluster_wr = meta.get("cluster_win_rate")
    risk_level = meta.get("risk_level")
    noise_pct = meta.get("noise_pct")
    drift_initial = meta.get("initial_score")
    drift_current = meta.get("current_score")
    drift_dur = meta.get("duration_min")
    em_pct = meta.get("expected_move_pct")
    em_prob = meta.get("probability")

    # Строим body по типу сигнала
    if name == "whale":
        if whale_value:
            body = f"Крупный перевод ${whale_value:,.0f} — сигнал 'умных денег'"
            triggers.append(f"Сумма: ${whale_value:,.0f}")
        elif whale_amount:
            body = f"Крупный перевод {whale_amount} {sym} — возможна манипуляция"
            triggers.append(f"Объём: {whale_amount} {sym}")
        else:
            body = "Крупный перевод средств — сигнал китов"

    elif name == "volume_spike":
        vr = volume_spike if volume_spike is not None else meta.get("volume_ratio", 0)
        body = f"Всплеск объёма в {vr:.1f}x от среднего — подтверждение движения"
        triggers.append(f"Объём ×{vr:.1f}")

    elif name == "whale_accum" or name == "cluster_buy":
        body = f"Аккумуляция позиции — 'умные деньги' входят"
        triggers.append(f"Оценка: {score}/100")

    elif name == "bid_ask_ratchet" or name == "orderbook":
        body = f"Дисбаланс стакана — давление {'покупателей' if direction == 'buy' else 'продавцов'}"
        triggers.append(f"Направление: {direction}")

    elif name == "wall_bounce" or name == "wall_stacked":
        if wall_size:
            body = f"Крупная стена ${wall_size:,.0f} — {'поддержка' if direction == 'buy' else 'сопротивление'}"
            triggers.append(f"Стена: ${wall_size:,.0f}")
        else:
            body = "Обнаружена крупная стена в стакане"

    elif name == "cvd_divergence":
        body = "Дивергенция CVD — цена и поток объёмов расходятся"
        triggers.append(f"CVD: {cvd_div or 0:+.0f}")

    elif name == "liquidation":
        if liq_total:
            body = f"Массовая ликвидация {'лонгов' if liq_side == 'buy' else 'шортов'} на ${liq_total:,.0f}"
            triggers.append(f"Ликвидации: ${liq_total:,.0f}")
        else:
            body = "Волна ликвидаций — возможна каскадная распродажа"

    elif name == "liquidation_cascade":
        body = "Каскад ликвидаций — цепная реакция"
        triggers.append("⚠ Высокий риск")

    elif name == "liquidation_cluster":
        body = "Кластер ликвидаций — зона высокой волатильности"
        triggers.append("Зона: ±1%")

    elif name == "squeeze_setup":
        body = "Сжатие Боллинджера — готовится сильное движение"
        triggers.append(f"Score: {score}/100")

    elif name == "market_breadth":
        pct_green = meta.get("pct_green", 50)
        if pct_green >= 80:
            body = f"Рынок перегрет: {pct_green:.0f}% монет в зелёной зоне"
        elif pct_green <= 20:
            body = f"Рынок перепродан: только {pct_green:.0f}% монет в зелёной зоне"
        else:
            body = f"Ширина рынка: {pct_green:.0f}% монет растут"
        triggers.append(f"Зелёных: {pct_green:.0f}%")

    elif name == "rotation":
        body = f"Смена лидера — капитал перетекает в другой сектор"
        triggers.append("Ротация")

    elif name == "ai_cluster":
        if cluster_wr:
            body = f"Сигнал попал в сильный кластер #{cluster_id} (win rate {cluster_wr:.0f}%)"
            triggers.append(f"Cluster WR: {cluster_wr:.0f}%")

    elif name == "pattern_similarity":
        wr = meta.get("avg_win_rate", 0)
        similar = meta.get("similar_count", 0)
        body = f"Паттерн похож на {similar} исторических (win rate {wr:.0f}%)"
        triggers.append(f"Win rate: {wr:.0f}%")

    elif name == "expected_move":
        if em_pct:
            body = f"Ожидаемое движение ±{em_pct:.2f}% с вероятностью {em_prob*100:.0f}%"
            triggers.append(f"±{em_pct:.2f}%")

    elif name == "risk_meter":
        body = f"Уровень риска: {risk_level} ({meta.get('risk_score', 0):.0f}/100)"
        triggers.append(f"Риск: {risk_level}")

    elif name == "noise_filter":
        if noise_pct:
            body = f"Рынок шумный (Noise {noise_pct:.0f}%) — сигналы могут быть ложными"
            triggers.append(f"Noise: {noise_pct:.0f}%")

    elif name == "confidence_drift":
        if drift_initial is not None and drift_current is not None and drift_dur is not None:
            body = f"Уверенность падает: {drift_initial:.0f} → {drift_current:.0f} за {drift_dur} мин"
            triggers.append(f"Дрифт: {drift_initial:.0f} → {drift_current:.0f}")

    elif name == "signal_lifecycle":
        stage = meta.get("stage", "born")
        stage_icon = {"born": "🌟", "growing": "📈", "confirmed": "✅", "weakening": "⚠️", "dead": "💀"}
        body = f"Стадия сигнала: {stage_icon.get(stage, '•')} {stage}"
        triggers.append(f"Stage: {stage}")

    elif name == "trend_strength":
        td = meta.get("trend_direction", "neutral")
        ts = meta.get("trend_strength", 50)
        body = f"Тренд {'восходящий' if td == 'up' else 'нисходящий'} (сила {ts:.0f})"
        triggers.append(f"{td} ({ts:.0f})")

    elif name == "heatmap":
        cat = meta.get("category", "")
        body = f"Аномалия в категории {cat}"
        triggers.append(cat)

    elif name == "liquidity_zone":
        sup = meta.get("nearest_support", 0)
        res = meta.get("nearest_resistance", 0)
        body = f"Зона ликвидности: поддержка {sup:.2f} / сопротивление {res:.2f}"
        triggers.append(f"S={sup:.2f} R={res:.2f}")

    elif name == "sector_rotation" or name == "sector_strength":
        sector = meta.get("sector", meta.get("top_sectors", ["unknown"])[0])
        body = f"Сектор {sector} — {'сильный' if direction == 'buy' else 'слабый'}"
        triggers.append(sector)

    elif name == "rs_momentum" or name == "rs_ranking":
        rank = meta.get("rank")
        if rank is None:
            rank = meta.get("rs_rank", 0)
        body = f"Относительная сила: rank #{rank}"
        triggers.append(f"Rank #{rank}")

    elif name == "divergence" or name == "market_alignment" or name == "market_leader":
        body = f"Корреляционный анализ: {'бычий' if direction == 'buy' else 'медвежий'} сигнал"
        triggers.append(f"Dir: {direction}")

    elif name == "taker_flow":
        ratio = taker_buy or meta.get("taker_buy_ratio", 0.5)
        body = f"Поток агрессивных сделок: buy/sell ratio {ratio:.2f}"
        triggers.append(f"B/S: {ratio:.2f}")

    elif name == "spread_widening":
        body = "Расширение спреда — снижение ликвидности"
        triggers.append("Spread ↑")

    elif name == "momentum":
        body = "Моментум — сильное направленное движение"
        triggers.append(f"Score: {score}/100")

    else:
        # Generic
        dir_text = {"buy": "бычий", "sell": "медвежий", "neutral": "нейтральный"}
        body = f"Сигнал {name}: {dir_text.get(direction, direction)} ({score}/100)"
        if meta.get("description"):
            body = meta["description"]

    if not body:
        body = f"Сигнал {name} ({score}/100)"

    # Price context
    if price > 0:
        context_list.append(f"Цена: ${price:,.2f}")
    if price_change:
        change = meta.get("price_change_pct", price_change)
        if isinstance(change, (int, float)):
            context_list.append(f"Change: {change:+.2f}%")

    return Explanation(
        signal_name=name,
        symbol=sym,
        score=score,
        direction=direction,
        headline=headline,
        body=body,
        triggers=triggers,
        context=context_list,
    )


def explain(sig: SignalResult, ticker: dict | None = None) -> Explanation:
    """Основной entry-point: сгенерировать объяснение для сигнала."""
    meta = sig.meta or {}
    return _fmt(sig, meta, ticker)


def format_explanation(ex: Explanation) -> str:
    """Форматировать объяснение для Telegram."""
    parts = [ex.headline]
    if ex.body:
        parts.append(f"└ {ex.body}")
    if ex.triggers:
        parts.append(f"  • {' • '.join(ex.triggers)}")
    if ex.context:
        parts.append(f"  📊 {' | '.join(ex.context)}")
    return "\n".join(parts)
