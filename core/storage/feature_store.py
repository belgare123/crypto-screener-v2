# Алиас на существующий FeatureStore
# FeatureStore живёт в core.features.store и сюда не дублируется.
from core.features.store import FeatureStore, get_feature_store  # noqa: F401

__all__ = ["FeatureStore", "get_feature_store"]
