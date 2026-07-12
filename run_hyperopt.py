#!/usr/bin/env python3
"""
Hyperopt (Optuna) – автоматический подбор параметров стратегии.
Использует run_backtest.py как целевую функцию.

Usage:
    python run_hyperopt.py --symbol BTC/USDT:USDT --interval 1m \\
        --start 2026-06-01 --end 2026-06-30 --metric profit_factor --n-trials 50
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import optuna
from optuna.pruners import MedianPruner
from optuna.samplers import TPESampler
from optuna.trial import Trial

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
)
logger = logging.getLogger("hyperopt")

BACKTEST_SCRIPT = Path(__file__).parent / "run_backtest.py"
STATS_FILE = Path("backtest_stats.json")


class HyperoptRunner:
    """
    Запускает оптимизацию параметров стратегии через Optuna.
    """

    # ── Параметры по умолчанию (можно переопределить в CLI) ──
    DEFAULT_SEARCH_SPACE = {
        "momentum_threshold": {"type": "float", "low": 0.3, "high": 2.0, "step": 0.1},
        "min_consecutive": {"type": "int", "low": 2, "high": 6, "step": 1},
    }

    def __init__(
        self,
        symbol: str = "BTC/USDT:USDT",
        interval: str = "1h",
        start_date: str | None = None,
        end_date: str | None = None,
        strategy_name: str = "momentum_v2",
        metric: str = "profit_factor",
        n_trials: int = 50,
        direction: str = "maximize",
        study_name: str = "momentum_optimization",
        storage: str = "sqlite:///optuna_studies.db",
        load_if_exists: bool = True,
        timeout: int = 120,
    ):
        self.symbol = symbol
        self.interval = interval
        self.start_date = start_date or (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        self.end_date = end_date or datetime.now().strftime("%Y-%m-%d")
        self.strategy_name = strategy_name
        self.metric = metric
        self.n_trials = n_trials
        self.direction = direction
        self.study_name = study_name
        self.storage = storage
        self.load_if_exists = load_if_exists
        self.timeout = timeout

        # Создаём / загружаем study
        self.study = optuna.create_study(
            study_name=study_name,
            storage=storage,
            direction=direction,
            sampler=TPESampler(seed=42),
            pruner=MedianPruner(n_startup_trials=5, n_warmup_steps=10),
            load_if_exists=load_if_exists,
        )

        existing = len(self.study.trials)
        logger.info("Study '%s' ready — %d existing trial(s).", study_name, existing)

    # ── Objective ──

    def objective(self, trial: Trial) -> float:
        """
        Целевая функция: предлагает параметры, запускает бэктест, возвращает метрику.
        """
        import optuna  # already imported at top, but keep local for clarity

        # 1. Предлагаем параметры из search space
        params = {}
        for pname, pcfg in self.DEFAULT_SEARCH_SPACE.items():
            if pcfg["type"] == "int":
                params[pname] = trial.suggest_int(pname, pcfg["low"], pcfg["high"], step=pcfg.get("step", 1))
            elif pcfg["type"] == "float":
                params[pname] = trial.suggest_float(pname, pcfg["low"], pcfg["high"], step=pcfg.get("step", None))
            else:
                params[pname] = trial.suggest_categorical(pname, pcfg.get("choices", []))

        # 2. Запускаем бэктест
        params_json = json.dumps(params)
        cmd = [
            sys.executable,
            str(BACKTEST_SCRIPT),
            "--mode", "parquet",
            "--symbol", self.symbol,
            "--interval", self.interval,
            "--start", self.start_date,
            "--end", self.end_date,
            "--strategy-params", params_json,
            "--quiet",
        ]

        t0 = time.time()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            elapsed = time.time() - t0
        except subprocess.TimeoutExpired:
            logger.warning("Trial %d timed out (%ds)", trial.number, self.timeout)
            return -1e9

        # 3. Парсим результат из backtest_stats.json
        try:
            if not STATS_FILE.exists():
                # fallback: парсим stderr/stdout
                if result.returncode != 0:
                    logger.debug("Trial %d exit=%d stderr=%s", trial.number, result.returncode, result.stderr[:200])
                return -1e9

            with open(STATS_FILE) as f:
                stats: dict[str, Any] = json.load(f)

            raw = stats.get(self.metric)
            if raw is None or raw == "inf" or raw == float("inf") or raw == -1:
                return -1e9

            value = float(raw)
            logger.info(
                "Trial %3d %s=%.4f (%.1fs) params=%s",
                trial.number, self.metric, value, elapsed, params,
            )
            return value

        except Exception as exc:
            logger.debug("Trial %d error: %s", trial.number, exc)
            return -1e9

    # ── Run ──

    def run(self) -> dict[str, Any]:
        """Запустить оптимизацию."""
        logger.info("Starting %d trials on %s %s …", self.n_trials, self.symbol, self.interval)
        self.study.optimize(self.objective, n_trials=self.n_trials, show_progress_bar=True)

        # ── Лучший результат ──
        best = self.study.best_trial
        logger.info("=" * 50)
        logger.info("BEST trial #%d  %s=%.6f", best.number, self.metric, best.value)
        logger.info("Params: %s", best.params)

        # Сохраняем
        best_path = Path("best_params.json")
        with open(best_path, "w") as f:
            json.dump(best.params, f, indent=2)
        logger.info("Saved → %s", best_path)

        # Все trials → CSV
        df = self.study.trials_dataframe()
        csv_path = Path("optuna_trials.csv")
        df.to_csv(csv_path, index=False)
        logger.info("Saved → %s  (%d rows)", csv_path, len(df))

        # Top‑5
        print("\n═══════════ TOP 5 TRIALS ═══════════")
        sorted_trials = sorted(self.study.trials, key=lambda t: t.value or -1e9, reverse=True)
        for i, t in enumerate(sorted_trials[:5]):
            print(f"  {i+1}. Trial #{t.number:3d}  {self.metric}={t.value:.4f}  {t.params}")
        print("═══════════════════════════════════════\n")

        return best.params

    # ── Альтернатива: ручной перебор (для smoke‑test) ──

    def grid_search(self, param_grid: dict[str, list]) -> None:
        """Простой grid‑search по заданной сетке (без Optuna)."""
        from itertools import product

        keys = list(param_grid.keys())
        best_val = -1e9
        best_params = None

        for values in product(*param_grid.values()):
            params = dict(zip(keys, values))
            trial_mock = type("TrialMock", (), {"number": 0, "suggest_int": lambda s, n, a, b, step=1: params[n],
                                                 "suggest_float": lambda s, n, a, b, step=None: params[n]})()
            val = self.objective(trial_mock)
            if val > best_val:
                best_val, best_params = val, params
        logger.info("Grid best: %s → %.4f", best_params, best_val)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hyperopt — автоматический подбор параметров стратегии",
    )
    parser.add_argument("--symbol", default="BTC/USDT:USDT")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--start", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", help="End date YYYY-MM-DD")
    parser.add_argument("--strategy", default="momentum_v2")
    parser.add_argument(
        "--metric",
        default="profit_factor",
        choices=["profit_factor", "sharpe_ratio", "total_pnl", "win_rate", "return_pct"],
    )
    parser.add_argument("--n-trials", type=int, default=50)
    parser.add_argument("--study-name", default="momentum_optimization")
    parser.add_argument("--storage", default="sqlite:///optuna_studies.db")
    parser.add_argument("--timeout", type=int, default=120, help="Per‑trial timeout (s)")
    parser.add_argument(
        "--grid",
        nargs="*",
        metavar="PARAM=VAL1,VAL2",
        help="Grid‑search: e.g. --grid momentum_threshold=0.3,0.5,1.0 min_consecutive=2,4",
    )
    args = parser.parse_args()

    runner = HyperoptRunner(
        symbol=args.symbol,
        interval=args.interval,
        start_date=args.start,
        end_date=args.end,
        strategy_name=args.strategy,
        metric=args.metric,
        n_trials=args.n_trials,
        study_name=args.study_name,
        storage=args.storage,
        timeout=args.timeout,
    )

    if args.grid:
        param_grid: dict[str, list] = {}
        for item in args.grid:
            name, vals = item.split("=", 1)
            param_grid[name] = [v.strip() for v in vals.split(",")]
        logger.info("Grid search: %s", param_grid)
        runner.grid_search(param_grid)
    else:
        runner.run()


if __name__ == "__main__":
    main()
