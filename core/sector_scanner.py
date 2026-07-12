"""
Sector Scanner — группирует активы по секторам и отслеживает ротацию.

Sectors (по топ-10):
  L1:   BTC, ETH, SOL, ADA, AVAX
  Meme: DOGE
  DeFi: LINK
  Layer0: SUI (инфра/Move)
  Payment: XRP
  Ecosystem: DOT
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  Sector Definition
# ──────────────────────────────────────────────

SECTOR_MAP: dict[str, list[str]] = {
    "L1": ["BTC", "ETH", "SOL", "ADA", "AVAX"],
    "Meme": ["DOGE"],
    "DeFi": ["LINK"],
    "Infra": ["SUI"],
    "Payment": ["XRP"],
    "Ecosystem": ["DOT"],
}

# Reverse map: symbol → sector
SYMBOL_TO_SECTOR: dict[str, str] = {}
for sector, symbols in SECTOR_MAP.items():
    for sym in symbols:
        SYMBOL_TO_SECTOR[sym] = sector


# ──────────────────────────────────────────────
#  Sector Snapshot
# ──────────────────────────────────────────────

@dataclass
class SectorSnapshot:
    timestamp: float = 0.0
    sectors: dict[str, dict] = field(default_factory=dict)  # sector → {avg_rs_5m, avg_rs_15m, members, leaders}
    leading_sector: str | None = None
    lagging_sector: str | None = None
    rotation_detected: bool = False


# ──────────────────────────────────────────────
#  Sector Scanner Engine
# ──────────────────────────────────────────────

class SectorScannerEngine:
    """Следит за движением по секторам."""

    def __init__(self):
        self._last_leading: str | None = None

    def scan(self, rankings: list[dict]) -> SectorSnapshot:
        """
        Принимает rankings из RelativeStrengthEngine.get_ranking()
        и вычисляет показатели по секторам.
        """
        sector_data: dict[str, list] = defaultdict(list)

        for item in rankings:
            sym_short = item["symbol"].split("/")[0]
            sector = SYMBOL_TO_SECTOR.get(sym_short)
            if sector:
                sector_data[sector].append(item)

        result: dict[str, dict] = {}
        for sector, members in sector_data.items():
            avg_rs_5m = sum(m.get("rs_5m_pct", 0) for m in members) / len(members) if members else 0
            avg_rs_15m = sum(m.get("rs_15m_pct", 0) for m in members) / len(members) if members else 0
            # Лидер сектора — с макс RS 5m
            leaders = sorted(members, key=lambda m: m.get("rs_5m_pct", 0), reverse=True)

            result[sector] = {
                "avg_rs_5m": round(avg_rs_5m, 2),
                "avg_rs_15m": round(avg_rs_15m, 2),
                "member_count": len(members),
                "members": [m["symbol"].split("/")[0] for m in members],
                "top_member": leaders[0]["symbol"].split("/")[0] if leaders else None,
                "top_rs_5m": leaders[0]["rs_5m_pct"] if leaders else 0,
                "direction": "up" if avg_rs_5m > 0 else ("down" if avg_rs_5m < 0 else "flat"),
            }

        # Лидирующий / отстающий сектор
        if result:
            sorted_by_rs_5m = sorted(result.items(), key=lambda kv: kv[1]["avg_rs_5m"], reverse=True)
            leading = sorted_by_rs_5m[0][0]
            lagging = sorted_by_rs_5m[-1][0]

            rotation = leading != self._last_leading and self._last_leading is not None
            self._last_leading = leading
        else:
            leading = None
            lagging = None
            rotation = False

        return SectorSnapshot(
            timestamp=time.time(),
            sectors=result,
            leading_sector=leading,
            lagging_sector=lagging,
            rotation_detected=rotation,
        )


# singleton
_sector_engine: SectorScannerEngine | None = None


def get_sector_engine() -> SectorScannerEngine:
    global _sector_engine
    if _sector_engine is None:
        _sector_engine = SectorScannerEngine()
    return _sector_engine
