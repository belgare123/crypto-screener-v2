"""
Feature Engine — централизованное вычисление и кэширование признаков.
Level 2 в архитектуре ARCHITECTURE_V2.md.
"""

from core.features.store import FeatureStore, get_feature_store, reset_feature_store
from core.features.base import BaseFeatureCalculator
from core.features.engine import FeatureEngine, get_feature_engine, reset_feature_engine

__all__ = [
    "FeatureStore",
    "get_feature_store",
    "reset_feature_store",
    "BaseFeatureCalculator",
    "FeatureEngine",
    "get_feature_engine",
    "reset_feature_engine",
]
