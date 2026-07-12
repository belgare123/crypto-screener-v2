"""
SessionRule — фильтр торговых сессий / времени суток.

Блокирует сигналы в нежелательное время:
- Азиатская сессия (00:00-07:00 UTC) → REDUCE (меньшая ликвидность)
- Около weekly/daily close (пятница 20:00-23:59 UTC) → REDUCE
- Кастомный blacklist символов для конкретных часов
"""
from __future__ import annotations

from core.risk.models import RiskContext, RiskReason, RiskVerdict
from core.risk.rules import RiskRule


class SessionRule(RiskRule):
    name = "session"

    # Азиатская сессия (низкая вола для не-азиатских пар)
    asia_start: int = 0
    asia_end: int = 7
    reduce_asia: bool = True

    # Европейская/американская — норма
    # Пятничный вечер — перед выходными
    friday_reduce_hours: set[int] = frozenset({20, 21, 22, 23})

    # Полная блокировка
    block_hours: set[int] = frozenset()  # none by default
    block_weekdays: set[int] = frozenset()  # 0=Mon, 6=Sun

    def __init__(self, asia_start: int = 0, asia_end: int = 7,
                 reduce_asia: bool = True,
                 block_hours: set[int] | None = None,
                 block_weekdays: set[int] | None = None):
        self.asia_start = asia_start
        self.asia_end = asia_end
        self.reduce_asia = reduce_asia
        if block_hours:
            self.block_hours = set(block_hours)
        if block_weekdays:
            self.block_weekdays = set(block_weekdays)

    async def evaluate(self, ctx: RiskContext) -> RiskReason:
        import datetime
        now = datetime.datetime.fromtimestamp(ctx.ts / 1000, tz=datetime.timezone.utc)

        hour = now.hour
        weekday = now.weekday()  # 0=Mon

        # Blocked hours
        if hour in self.block_hours:
            return RiskReason(
                self.name, RiskVerdict.BLOCK,
                f"hour {hour}:00 is blocked",
                severity=1.0,
                extra={"hour": hour},
            )

        # Blocked weekdays
        if weekday in self.block_weekdays:
            return RiskReason(
                self.name, RiskVerdict.BLOCK,
                f"weekday {weekday} is blocked",
                severity=1.0,
                extra={"weekday": weekday},
            )

        # Friday close
        if weekday == 4 and hour in self.friday_reduce_hours:
            return RiskReason(
                self.name, RiskVerdict.REDUCE,
                f"Friday close session (hour {hour}), low liquidity ahead of weekend",
                severity=0.4,
                extra={"hour": hour, "weekday": weekday},
            )

        # Asia session
        if self.reduce_asia and self.asia_start <= hour < self.asia_end:
            return RiskReason(
                self.name, RiskVerdict.REDUCE,
                f"Asia session (hour {hour}), lower liquidity",
                severity=0.2,
                extra={"hour": hour},
            )

        return RiskReason(self.name, RiskVerdict.ALLOW,
                          f"session OK (hour {hour})")
