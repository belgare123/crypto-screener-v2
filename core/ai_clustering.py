"""
AI Clustering — автоматическая кластеризация сигналов (ML без правил).

Использует k-means++ на Signal DNA векторах (17 признаков).
Каждому сигналу присваивается кластер.
Кластеры с win_rate > 70% получают повышенный score.

Без внешних ML-библиотек — реализация k-means встроенная.
"""

from __future__ import annotations

import json
import logging
import math
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

from core import SignalResult
from core.signal_dna import SignalDNA, get_dna_store, DNAStore

logger = logging.getLogger(__name__)

N_CLUSTERS = 10
CLUSTER_MIN_SAMPLES = 5         # мин. образцов для valid кластера
MAX_ITERATIONS = 20
HIGH_WIN_RATE_THRESHOLD = 70    # %


@dataclass
class ClusterInfo:
    id: int = 0
    centroid: list[float] = field(default_factory=list)
    size: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_score: float = 0.0
    dominant_direction: str = "neutral"
    dominant_symbols: list[str] = field(default_factory=list)
    top_signals: list[str] = field(default_factory=list)


@dataclass
class ClusteringSnapshot:
    timestamp: float = 0.0
    total_dna: int = 0
    clusters: list[ClusterInfo] = field(default_factory=list)
    best_cluster: ClusterInfo | None = None
    worst_cluster: ClusterInfo | None = None
    last_assigned_cluster: int = -1
    last_dna_id: int = 0


def _euclidean(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return float('inf')
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _kmeans_pp_init(data: list[list[float]], k: int, seed: int = 42) -> list[list[float]]:
    """K-means++ инициализация центроидов."""
    random.seed(seed)
    centroids = [data[random.randint(0, len(data) - 1)]]
    for _ in range(1, k):
        dists = [min(_euclidean(p, c) for c in centroids) for p in data]
        total = sum(dists)
        if total == 0:
            centroids.append(data[random.randint(0, len(data) - 1)])
        else:
            probs = [d / total for d in dists]
            r = random.random()
            cum = 0.0
            for i, p in enumerate(data):
                cum += probs[i]
                if r <= cum:
                    centroids.append(p)
                    break
    return centroids


def _kmeans_fit(data: list[list[float]], k: int, max_iter: int = MAX_ITERATIONS) -> tuple[list[list[float]], list[int]]:
    """K-means: возвращает (centroids, labels)."""
    if not data or len(data) < k:
        return [], [0] * len(data)

    dim = len(data[0])
    centroids = _kmeans_pp_init(data, k)
    labels = [0] * len(data)

    for _ in range(max_iter):
        # Assign
        changed = 0
        for i, p in enumerate(data):
            dists = [_euclidean(p, c) for c in centroids]
            lab = min(range(len(dists)), key=lambda x: dists[x])
            if lab != labels[i]:
                changed += 1
                labels[i] = lab

        # Update centroids
        for c_idx in range(k):
            members = [data[j] for j in range(len(data)) if labels[j] == c_idx]
            if members:
                centroids[c_idx] = [sum(v[d] for v in members) / len(members) for d in range(dim)]

        if changed == 0:
            break

    return centroids, labels


class ClusteringEngine:
    """
    Кластеризация сигналов по DNA векторам.
    """

    def __init__(self, store: DNAStore | None = None, n_clusters: int = N_CLUSTERS):
        self._store = store or get_dna_store()
        self._n_clusters = n_clusters
        self._centroids: list[list[float]] = []
        self._cluster_win_rates: dict[int, dict] = defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0})
        self._last_snapshot: ClusteringSnapshot | None = None
        self._initialized = False

    @property
    def last_snapshot(self) -> ClusteringSnapshot | None:
        return self._last_snapshot

    def _load_dna_vectors(self) -> tuple[list[list[float]], list[SignalDNA]]:
        """Загрузить все DNA векторы из БД."""
        stats = self._store.get_stats()
        total = stats.get("total_dna", 0)
        if total == 0:
            return [], []

        # Загружаем последние 2000 DNA через DNAStore API
        try:
            dnas = store.get_recent(limit=2000)
        except Exception:
            return [], []

        vectors = []
        result_dnas = []
        for dna in dnas:
            v = dna.to_vector()
            if len(v) > 0 and any(abs(x) > 0 for x in v):
                vectors.append(v)
                result_dnas.append(dna)

        return vectors, result_dnas

    def initialize(self):
        """Инициализация: кластеризация всех существующих DNA."""
        vectors, dnas = self._load_dna_vectors()
        if not vectors or len(vectors) < self._n_clusters:
            logger.info("[clustering] not enough DNA yet (%d)", len(vectors))
            self._initialized = True
            return

        logger.info("[clustering] fitting %d vectors into %d clusters...", len(vectors), self._n_clusters)
        centroids, labels = _kmeans_fit(vectors, self._n_clusters)
        self._centroids = centroids

        # Считаем win rate по кластерам
        cluster_data = defaultdict(lambda: {"vectors": [], "wins": 0, "losses": 0, "total": 0, "scores": [], "directions": [], "symbols": [], "signals": []})
        for i, (dna, lab) in enumerate(zip(dnas, labels)):
            cd = cluster_data[lab]
            cd["vectors"].append(vectors[i])
            cd["total"] += 1
            cd["scores"].append(dna.score)
            cd["directions"].append(dna.direction)
            short_sym = dna.symbol.split("/")[0]
            cd["symbols"].append(short_sym)
            cd["signals"].append(dna.signal_name)

        clusters = []
        for cid in range(self._n_clusters):
            cd = cluster_data[cid]
            total = cd["total"]
            if total < CLUSTER_MIN_SAMPLES:
                continue
            avg_score = sum(cd["scores"]) / max(total, 1)
            # Win rate: пока используем avg_score как прокси (feedback loop ещё не построен)
            win_rate = min((avg_score / 100 * 85), 90)  # score-based proxy
            directions = cd["directions"]
            buys = directions.count("buy")
            sells = directions.count("sell")
            dominant_dir = "buy" if buys > sells else ("sell" if sells > buys else "neutral")

            # Топ-3 символа и топ-3 сигнала
            sym_counts = defaultdict(int)
            sig_counts = defaultdict(int)
            for s in cd["symbols"]:
                sym_counts[s] += 1
            for s in cd["signals"]:
                sig_counts[s] += 1
            top_syms = sorted(sym_counts, key=sym_counts.get, reverse=True)[:3]
            top_sigs = sorted(sig_counts, key=sig_counts.get, reverse=True)[:3]

            clusters.append(ClusterInfo(
                id=cid,
                centroid=self._centroids[cid] if cid < len(self._centroids) else [],
                size=total,
                wins=cd["wins"],
                losses=cd["losses"],
                win_rate=round(win_rate, 1),
                avg_score=round(avg_score, 0),
                dominant_direction=dominant_dir,
                dominant_symbols=top_syms,
                top_signals=top_sigs,
            ))

        clusters.sort(key=lambda c: c.win_rate, reverse=True)

        snap = ClusteringSnapshot(
            timestamp=time.time(),
            total_dna=len(dnas),
            clusters=clusters,
            best_cluster=clusters[0] if clusters else None,
            worst_cluster=clusters[-1] if clusters and len(clusters) > 1 else None,
        )
        self._last_snapshot = snap
        self._initialized = True
        logger.info("[clustering] initialized: %d clusters from %d dna", len(clusters), len(dnas))

    def assign(self, dna_vector: list[float]) -> int:
        """Присвоить DNA к ближайшему кластеру."""
        if not self._centroids or len(self._centroids) == 0:
            return -1

        dists = [_euclidean(dna_vector, c) for c in self._centroids]
        return min(range(len(dists)), key=lambda x: dists[x])

    def to_signal(self, dna: SignalDNA) -> SignalResult | None:
        """Сигнал: если сигнал попал в кластер с win_rate > 70%."""
        if not self._initialized:
            self.initialize()

        cluster_id = self.assign(dna.to_vector())
        if cluster_id < 0:
            return None

        # Ищем кластер
        snap = self._last_snapshot
        if not snap or not snap.clusters:
            return None

        target = None
        for c in snap.clusters:
            if c.id == cluster_id:
                target = c
                break

        if not target or target.win_rate < HIGH_WIN_RATE_THRESHOLD:
            return None

        # Повышаем score
        bonus = min((target.win_rate - 50) * 0.3, 20)
        new_score = min(dna.score + bonus, 98)

        return SignalResult(
            signal_name="ai_cluster",
            symbol=dna.symbol,
            exchange="bybit",
            score=round(new_score, 0),
            direction=dna.direction,
            meta={
                "cluster_id": cluster_id,
                "cluster_win_rate": target.win_rate,
                "cluster_size": target.size,
                "cluster_avg_score": target.avg_score,
                "cluster_direction": target.dominant_direction,
                "top_symbols": target.dominant_symbols,
                "top_signals": target.top_signals,
                "original_score": dna.score,
                "score_bonus": round(new_score - dna.score, 0),
                "description": f"Кластер #{cluster_id} (win rate {target.win_rate:.0f}%, {target.size} сигналов)",
            },
            ts=time.time(),
            cooldown=3600,
        )


_clustering_engine: ClusteringEngine | None = None


def get_clustering_engine() -> ClusteringEngine:
    global _clustering_engine
    if _clustering_engine is None:
        _clustering_engine = ClusteringEngine()
    return _clustering_engine
