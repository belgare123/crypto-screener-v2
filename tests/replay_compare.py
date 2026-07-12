"""
replay_compare — PHASE 0 TOOL (ARCHIVED in v0.10.0).

В v0.10.0 V1 SignalEngine и сигналы удалены из кодовой базы.
Этот инструмент был предназначен для сравнения V1->V2 в переходный период.

Оставлен как заглушка для обратной совместимости.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    print("replay_compare is archived in v0.10.0 (V1 removed).")
    sys.exit(0)
