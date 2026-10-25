"""Schema element extraction, BM25 / dense / value indexes."""

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Element records
# ---------------------------------------------------------------------------

SAMPLE_TRUNC = 40
MAX_SAMPLES = 5


@dataclass
class SchemaElement:
    """One schema element: a table (column="") or a column."""
    table: str
    column: str  # "" for table-level record
    dtype: str = ""
    is_pk: bool = False
    fk_target: Optional[str] = None  # "table.column" or "table"
    description: str = ""
    sample_values: List[str] = field(default_factory=list)
    # runtime ranking
    rank: int = -1
    score: float = 0.0
    # extra annotation from S6
    matched_value: Optional[str] = None

    def __post_init__(self):
        self.sample_values = [
            (v or "")[:SAMPLE_TRUNC] for v in self.sample_values[:MAX_SAMPLES]
        ]

    @property
    def is_table(self) -> bool:
        return self.column == ""

    @property
    def element_id(self) -> str:
        if self.is_table:
            return f"T:{self.table}"
        return f"C:{self.table}.{self.column}"

    def bm25_text(self) -> str:
        """text = "table column description values", underscores split, lowercased."""
        parts = [self.table]
        if not self.is_table:
            parts.append(self.column)
        if self.description:
            parts.append(self.description)
        parts.extend(self.sample_values)
        raw = " ".join(parts)
        # split underscores then lowercase
        raw = raw.replace("_", " ")
        return raw.lower()

    def serialise_line(self) -> str:
        """CREATE TABLE-style column line."""
        if self.is_table:
            return f"CREATE TABLE {self.table} ("
        line = f"    {self.column} {self.dtype or 'TEXT'}"
        if self.is_pk:
            line += " PRIMARY KEY"
        if self.matched_value:
            line += f"  -- matched value: '{self.matched_value}'"
        return line

    def sample_comment(self) -> str:
        if self.sample_values:
            return f"    -- e.g. {', '.join(self.sample_values)}"
        return ""


# ---------------------------------------------------------------------------
# Schema extraction from metadata + sqlite
# ---------------------------------------------------------------------------

def extract_elements(
    schema_meta: Dict[str, Any],
    db_path: str,
) -> List[SchemaElement]:
    """Build element records from dataset schema metadata and the sqlite file.

    schema_meta: Spider tables.json entry (or BIRD dev_tables entry).
    """
    # normalise column info
    table_names = schema_meta.get("table_names_original") or schema_meta.get("table_names", [])
    col_entries = schema_meta.get("column_names_original") or schema_meta.get("column_names", [])
    col_types = schema_meta.get("column_types", [])
    pk_info = schema_meta.get("primary_keys", [])
    fk_info = schema_meta.get("foreign_keys", [])
    # descriptions (BIRD has column_names_evaluation? usually none in tables.json)
    descriptions = schema_meta.get("column_descriptions", {})

    # map table -> columns
    # col entry: [table_idx, col_name] or (table_idx, col_name)
    table_cols: Dict[int, List[Tuple[int, str]]] = {}
    for i, entry in enumerate(col_entries):
        ti, cname = entry[0], entry[1]
        table_cols.setdefault(ti, []).append((i, cname))

    # PK sets (primary_keys: flat list of ints, list of lists per table, or
    # mixed ints/lists as produced by BIRD).  Normalize into a flat set.
    pk_col_indices = _normalize_pk_indices(pk_info)

    # FK list: list of [c1, c2] column-index pairs
    fk_pairs = [(f[0], f[1]) for f in fk_info] if fk_info else []

    # gather sample values from sqlite
    samples = _collect_samples(db_path, table_names, col_entries)

    elements: List[SchemaElement] = []
    for ti, tname in enumerate(table_names):
        elements.append(SchemaElement(table=tname, column="", dtype="TABLE"))

        for (ci, cname) in table_cols.get(ti, []):
            if cname == "*":
                continue
            dtype = col_types[ci] if ci < len(col_types) else "text"
            is_pk = ci in pk_col_indices
            # find FK target: [c1, c2] means c1 references c2 (Spider convention)
            fk_target = None
            for c1, c2 in fk_pairs:
                if c1 == ci:
                    tgt_t = _col_table(col_entries, table_names, c2)
                    tgt_c = col_entries[c2][1]
                    fk_target = f"{tgt_t}.{tgt_c}" if tgt_t else tgt_c
                    break

            desc = descriptions.get(cname, "") if isinstance(descriptions, dict) else ""
            elements.append(SchemaElement(
                table=tname, column=cname, dtype=dtype,
                is_pk=is_pk, fk_target=fk_target, description=desc,
                sample_values=samples.get((tname, cname), []),
            ))
    return elements


def _col_table(col_entries, table_names, idx):
    if 0 <= idx < len(col_entries):
        ti = col_entries[idx][0]
        if 0 <= ti < len(table_names):
            return table_names[ti]
    return None


def _normalize_pk_indices(pk_info):
    """Normalize primary-key metadata into a flat set of column indices.

    Supports the formats used by the supported datasets:
      - flat list of ints:            [1, 4, 8]          (Spider)
      - list of lists per table:      [[0], [1], [2]]
      - mixed ints and lists:         [1, 4, [19, 20]]   (BIRD)
      - empty / None:                 []

    Composite primary keys (a list entry with multiple indices) are preserved
    by flattening every index into the returned set.  Nothing is silently
    discarded.
    """
    indices = set()
    if not pk_info:
        return indices
    for entry in pk_info:
        if isinstance(entry, (list, tuple)):
            for ci in entry:
                indices.add(ci)
        else:
            indices.add(entry)
    return indices


def _collect_samples(db_path: str, table_names: List[str],
                     col_entries: List) -> Dict[Tuple[str, str], List[str]]:
    """Up to 5 distinct string values per column, truncated to 40 chars."""
    out: Dict[Tuple[str, str], List[str]] = {}
    try:
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.text_factory = lambda b: b.decode("utf-8", "replace")
        for ti, tname in enumerate(table_names):
            try:
                cols = [row[1] for row in
                        conn.execute(f"PRAGMA table_info('{tname}')").fetchall()]
                for c in cols:
                    try:
                        rows = conn.execute(
                            f"SELECT DISTINCT CAST(\"{c}\" AS TEXT) FROM \"{tname}\" "
                            f"WHERE \"{c}\" IS NOT NULL AND TRIM(CAST(\"{c}\" AS TEXT)) != '' "
                            f"LIMIT {MAX_SAMPLES * 3}"
                        ).fetchall()
                        vals = []
                        seen = set()
                        for r in rows:
                            v = (r[0] or "")[:SAMPLE_TRUNC]
                            if v and v.lower() not in seen:
                                seen.add(v.lower())
                                vals.append(v)
                            if len(vals) >= MAX_SAMPLES:
                                break
                        out[(tname, c)] = vals
                    except Exception:
                        continue
            except Exception:
                continue
        conn.close()
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# Index building
# ---------------------------------------------------------------------------

class DBIndex:
    """Per-database indexes: BM25, dense embeddings, value index."""

    def __init__(self, db_name: str, elements: List[SchemaElement]):
        self.db_name = db_name
        self.elements = elements
        self.bm25 = None
        self.dense_embs: Optional[np.ndarray] = None  # (n, d) normalised
        self.value_index: Dict[str, List[Tuple[str, str, float]]] = {}
        # value_index: key = "table.column" lower -> [(value_lower, value_orig, score)]
        self._build_bm25()
        self._build_value_index()

    def _build_bm25(self):
        from rank_bm25 import BM25Okapi
        tokenised = [e.bm25_text().split() for e in self.elements]
        # BM25Okapi needs at least 1 doc
        if tokenised:
            self.bm25 = BM25Okapi(tokenised)

    def _build_value_index(self):
        for e in self.elements:
            if e.is_table:
                continue
            key = f"{e.table}.{e.column}".lower()
            entries = []
            for v in e.sample_values:
                vl = v.lower().strip()
                if vl:
                    entries.append((vl, v, 1.0))
            if entries:
                self.value_index[key] = entries

    def build_dense(self, encoder):
        """encoder: callable List[str] -> np.ndarray (n, d). Normalise rows."""
        texts = [e.bm25_text() for e in self.elements]
        emb = encoder(texts)
        emb = np.asarray(emb, dtype=np.float32)
        norms = np.linalg.norm(emb, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self.dense_embs = emb / norms

    def bm25_scores(self, query: str) -> np.ndarray:
        tokens = query.replace("_", " ").lower().split()
        return np.asarray(self.bm25.get_scores(tokens), dtype=np.float64)

    def dense_scores(self, query_emb: np.ndarray) -> np.ndarray:
        if self.dense_embs is None:
            return np.zeros(len(self.elements))
        q = np.asarray(query_emb, dtype=np.float32).ravel()
        n = np.linalg.norm(q)
        if n == 0:
            return np.zeros(len(self.elements))
        q = q / n
        return self.dense_embs @ q

    def value_hits(self, query: str) -> Dict[str, float]:
        """Match quoted literals, Capitalised tokens, numerals in query
        against value index; returns {table.column_lower: best_score}.
        Uses rapidfuzz partial_ratio >= 90."""
        from rapidfuzz import fuzz

        candidates = _extract_query_values(query)
        hits: Dict[str, float] = {}
        for qv in candidates:
            qvl = qv.lower()
            for key, entries in self.value_index.items():
                for vl, _orig, _s in entries:
                    score = fuzz.partial_ratio(qvl, vl)
                    if score >= 90:
                        hits[key] = max(hits.get(key, 0.0), score / 100.0)
        return hits


def _extract_query_values(text: str) -> List[str]:
    """Quoted literals, Capitalised tokens, numerals."""
    vals = []
    # quoted literals
    vals.extend(re.findall(r"'([^']{1,60})'", text))
    vals.extend(re.findall(r'"([^"]{1,60})"', text))
    # numerals (including decimals)
    vals.extend(re.findall(r"\b\d+(?:\.\d+)?\b", text))
    # Capitalised tokens (at least 2 chars, not sentence-start-only heuristic)
    for m in re.findall(r"\b([A-Z][a-zA-Z]{1,}\b)", text):
        vals.append(m)
    return vals


# ---------------------------------------------------------------------------
# Encoder (BAAI/bge-small-en-v1.5)
# ---------------------------------------------------------------------------

_encoder_cache = {}


def get_encoder(model_name: str = "BAAI/bge-small-en-v1.5"):
    """Lazy-load sentence-transformers model (cached)."""
    if model_name in _encoder_cache:
        return _encoder_cache[model_name]
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(model_name)

    def encode(texts: List[str]) -> np.ndarray:
        return model.encode(
            texts, normalize_embeddings=True,
            show_progress_bar=False, convert_to_numpy=True,
        )

    _encoder_cache[model_name] = encode
    return encode
