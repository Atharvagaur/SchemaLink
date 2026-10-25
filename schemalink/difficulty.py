"""ABSL difficulty features and LightGBM tier classifier.

Labels come ONLY from the train split: the smallest tier at which S6 gets
the question right, else the top tier.  Features are computed before any
LLM call for the question being solved.
"""

import math
import random
import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from schemalink.indexer import DBIndex, SchemaElement, _extract_query_values
from schemalink.linkers.s3 import link_s3

ANALYTICAL_KEYWORDS = [
    "per", "average", "most", "highest", "lowest", "than", "each",
    "ratio", "percent", "difference", "both",
]

FEATURE_NAMES = [
    "q_word_count",
    "n_analytical_keywords",
    "n_quoted_literals",
    "n_numerals",
    "schema_size_n",
    "mean_fanout",
    "score_margin_top1_top10",
    "softmax_entropy_top50",
    "distinct_tables_top10",
    "n_value_hits",
]


def compute_features(
    question: str,
    evidence: str,
    elements: List[SchemaElement],
    db_index: Optional[DBIndex] = None,
    query_emb: Optional[np.ndarray] = None,
) -> np.ndarray:
    """10 features computed BEFORE any LLM call."""
    text = f"{question} {evidence}".strip()
    words = question.split()

    # 1 word count
    f_qwords = len(words)
    # 2 analytical keywords (word-level, punctuation stripped)
    tokens = re.findall(r"[a-z]+", question.lower())
    f_kw = sum(1 for tok in tokens for k in ANALYTICAL_KEYWORDS if tok == k)
    # 3 quoted literals
    f_quoted = len(re.findall(r"'[^']+'|\"[^\"]+\"", text))
    # 4 numerals
    f_nums = len(re.findall(r"\b\d+(?:\.\d+)?\b", text))
    # 5 schema size N (columns + tables)
    f_n = len(elements)
    # 6 mean table fan-out (columns per table, tables only)
    tbl_counts: Dict[str, int] = {}
    n_tables = set()
    for e in elements:
        n_tables.add(e.table)
        if not e.is_table:
            tbl_counts[e.table] = tbl_counts.get(e.table, 0) + 1
    f_fanout = (sum(tbl_counts.values()) / len(tbl_counts)) if tbl_counts else 0.0

    # 7-9 retrieval features from S3 scores
    f_margin = 0.0
    f_entropy = 0.0
    f_tables_top10 = 0.0
    if db_index is not None:
        ranked = link_s3(list(elements), question, evidence, db_index, query_emb)
        scores = np.array([e.score for e in ranked], dtype=np.float64)

        # margin: top-1 minus 10th (0 if fewer than 10)
        if len(scores) >= 10:
            f_margin = float(scores[0] - scores[9])
        elif len(scores) >= 2:
            f_margin = float(scores[0] - scores[-1])

        # normalised entropy of softmax over top-50 scores
        top50 = scores[:50]
        if len(top50) > 0 and np.any(top50 > 0):
            mx = top50.max()
            ex = np.exp(top50 - mx)
            p = ex / ex.sum()
            p = p[p > 0]
            ent = float(-(p * np.log(p)).sum())
            max_ent = math.log(len(top50)) if len(top50) > 1 else 1.0
            f_entropy = ent / max_ent if max_ent > 0 else 0.0

        # distinct tables in top-10
        top10 = ranked[:10]
        f_tables_top10 = float(len({e.table for e in top10}))

    # 10 value-index hits
    f_hits = 0.0
    if db_index is not None:
        f_hits = float(len(db_index.value_hits(text)))

    return np.array([
        f_qwords, f_kw, f_quoted, f_nums, f_n, f_fanout,
        f_margin, f_entropy, f_tables_top10, f_hits,
    ], dtype=np.float64)


# ---------------------------------------------------------------------------
# Tier labels (train split only)
# ---------------------------------------------------------------------------

def tier_budgets(dataset: str) -> List[int]:
    """B1, B2, B3 token budgets per tier."""
    if dataset == "spider":
        return [500, 1500, 4000]
    return [1000, 4000, 16000]


def make_labels(
    train_items: List[Dict[str, Any]],
    tier_correct: List[bool],
) -> np.ndarray:
    """tier_correct[i][t] = True if S6 at tier t produced correct SQL for
    train item i (t in 0..2).  Label = smallest correct tier, else top tier
    (index 2)."""
    labels = []
    for corrects in tier_correct:
        label = 2  # default top tier
        for t, ok in enumerate(corrects):
            if ok:
                label = t
                break
        labels.append(label)
    return np.array(labels, dtype=np.int32)


# ---------------------------------------------------------------------------
# LightGBM classifier
# ---------------------------------------------------------------------------

def train_classifier(
    X: np.ndarray,
    y: np.ndarray,
    seed: int = 0,
    val_frac: float = 0.2,
) -> Tuple[Any, float, List[List[int]]]:
    """Train LightGBM 3-class classifier.  Returns (model, val_accuracy,
    confusion_matrix 3x3)."""
    import lightgbm as lgb
    from sklearn.metrics import accuracy_score, confusion_matrix

    rng = random.Random(seed)
    idx = list(range(len(X)))
    rng.shuffle(idx)
    n_val = max(1, int(len(idx) * val_frac))
    val_idx = idx[:n_val]
    tr_idx = idx[n_val:]

    if len(set(y[tr_idx])) < 2:
        # degenerate: not enough classes in train
        model = None
        preds = np.full(len(val_idx), y[val_idx][0])
    else:
        model = lgb.LGBMClassifier(
            n_estimators=100, max_depth=5, learning_rate=0.1,
            num_class=3, objective="multiclass",
            random_state=seed, verbose=-1,
        )
        model.fit(X[tr_idx], y[tr_idx])
        preds = model.predict(X[val_idx])

    acc = float(accuracy_score(y[val_idx], preds))
    cm = confusion_matrix(y[val_idx], preds, labels=[0, 1, 2]).tolist()
    return model, acc, cm


def predict_tier(
    model: Any,
    features: np.ndarray,
    default_tier: int = 0,
) -> int:
    """Predict start tier (0-indexed).  Returns default when model is None
    or prediction fails."""
    if model is None:
        return default_tier
    try:
        pred = model.predict(features.reshape(1, -1))
        return int(pred[0])
    except Exception:
        return default_tier
