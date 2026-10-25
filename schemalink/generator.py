"""LLM generator: one fixed prompt, disk cache, retries, parallel calls."""

import hashlib
import json
import logging
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import tiktoken

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fixed prompt — IDENTICAL for every strategy
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = (
    "You are an expert SQLite analyst. Using ONLY the schema below, "
    "write one SQLite query that answers the question. Return only the SQL.\n"
    "### Schema\n{schema}\n"
    "### Evidence\n{evidence}\n"
    "### Question\n{question}\n"
    "### SQL\n"
)

_ENC = tiktoken.get_encoding("o200k_base")


def count_tokens(text: str) -> int:
    return len(_ENC.encode(text))


def build_messages(
    schema: str,
    question: str,
    evidence: str,
    exemplars: Optional[List[Dict[str, Any]]] = None,
    extra_note: str = "",
) -> List[Dict[str, str]]:
    """Build the message list: 3 few-shot user/assistant pairs then the query."""
    messages: List[Dict[str, str]] = []
    for ex in (exemplars or []):
        messages.append({"role": "user", "content": ex["prompt"]})
        messages.append({"role": "assistant", "content": ex["sql"]})

    user_prompt = PROMPT_TEMPLATE.format(
        schema=schema, evidence=evidence, question=question
    )
    if extra_note:
        user_prompt = user_prompt + "\n" + extra_note
    messages.append({"role": "user", "content": user_prompt})
    return messages


def extract_sql(text: str) -> str:
    """Extract SQL from code fences and strip trailing semicolons."""
    if not text:
        return ""
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if m:
        sql = m.group(1).strip()
    else:
        sql = text.strip()
    # strip trailing semicolons
    sql = sql.rstrip().rstrip(";").rstrip()
    return sql


# ---------------------------------------------------------------------------
# Few-shot exemplars (3 fixed, seeded, saved to exemplars.json)
# ---------------------------------------------------------------------------

def make_exemplars(
    train_questions: List[Dict[str, Any]],
    seed: int = 0,
    k: int = 3,
    exemplar_schema_tokens: int = 4000,
) -> List[Dict[str, Any]]:
    """Pick k fixed exemplars from the train split (seeded) and save."""
    rng = random.Random(seed)
    idx = list(range(len(train_questions)))
    rng.shuffle(idx)
    chosen = []
    for i in idx[:k]:
        q = train_questions[i]
        schema_text = full_schema_text(q.get("schema"), q.get("db_path"))
        prompt = PROMPT_TEMPLATE.format(
            schema=schema_text,
            evidence=q.get("evidence", ""),
            question=q["question"],
        )
        chosen.append({
            "qid": q["qid"],
            "prompt": prompt,
            "sql": q["gold_sql"],
        })
    return chosen


def full_schema_text(schema_meta: Optional[Dict[str, Any]], db_path: str) -> str:
    """Serialise the complete schema (for exemplars / S0)."""
    from schemalink.budget import serialise
    from schemalink.indexer import extract_elements
    if not schema_meta:
        return ""
    els = extract_elements(schema_meta, db_path)
    return serialise(els)


def save_exemplars(path: Path, exemplars: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(exemplars, f, indent=2, ensure_ascii=False)


def load_exemplars(path: Path) -> List[Dict[str, Any]]:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

class Generator:
    """Frozen generator: same model, prompt, exemplars, decoding params
    across all strategies.  Disk-cache keyed by hash of
    (model, messages, temperature, n, seed)."""

    def __init__(self, cfg: Dict[str, Any], cache_dir: str = "cache"):
        self.cfg = cfg
        gen = cfg["generator"]
        self.model = gen["model"]
        self.api_key = gen["api_key"]
        self.base_url = gen.get("base_url", "https://openrouter.ai/api/v1")
        self.max_tokens = gen.get("max_tokens", 512)
        self.timeout = gen.get("timeout", 60)
        self.workers = gen.get("workers", 4)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(
                base_url=self.base_url, api_key=self.api_key,
                timeout=self.timeout, max_retries=0,
            )
        return self._client

    # -- cache ---------------------------------------------------------------
    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    @staticmethod
    def _key(model, messages, temperature, n, seed) -> str:
        blob = json.dumps(
            {"m": model, "msg": messages, "t": temperature,
             "n": n, "s": seed},
            sort_keys=True, ensure_ascii=False,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    def _cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        p = self._cache_path(key)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def _cache_put(self, key: str, val: Dict[str, Any]) -> None:
        p = self._cache_path(key)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(val, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)

    # -- API call ------------------------------------------------------------
    def _call_once(
        self, messages, temperature: float, n: int, seed: int,
    ) -> Tuple[List[Dict[str, str]], int, int, bool]:
        """Single API call.  Returns (choices, prompt_tokens,
        completion_tokens, used_usage)."""
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=self.max_tokens,
            temperature=temperature,
            n=n,
            seed=seed,
        )
        used_usage = resp.usage is not None and getattr(resp.usage, "prompt_tokens", None) is not None
        if used_usage:
            ptok = resp.usage.prompt_tokens
        else:
            logger.warning(
                "API did not return usage.prompt_tokens; "
                "falling back to tiktoken count of full messages."
            )
            ptok = sum(count_tokens(m.get("content", "")) for m in messages)

        choices = []
        for ch in resp.choices:
            raw = ch.message.content or ""
            choices.append({
                "sql": extract_sql(raw),
                "raw": raw,
                "finish_reason": ch.finish_reason,
            })
        ctok = 0
        if used_usage and getattr(resp.usage, "completion_tokens", None) is not None:
            ctok = resp.usage.completion_tokens
        else:
            ctok = sum(count_tokens(c["raw"]) for c in choices)
        return choices, ptok, ctok, used_usage

    def generate(
        self,
        schema: str,
        question: str,
        evidence: str,
        temperature: float = 0.0,
        n: int = 1,
        seed: int = 0,
        exemplars: Optional[List[Dict[str, Any]]] = None,
        extra_note: str = "",
    ) -> Dict[str, Any]:
        """One generation call (with cache + retry).  Returns dict with
        choices, prompt_tokens, latency_s."""
        messages = build_messages(schema, question, evidence, exemplars, extra_note)
        key = self._key(self.model, messages, temperature, n, seed)
        cached = self._cache_get(key)
        if cached is not None:
            cached.setdefault("completion_tokens", 0)  # older cache entries
            return cached

        t0 = time.time()
        choices: List[Dict[str, str]] = []
        ptok = 0
        ctok = 0
        last_err = None
        for attempt in range(5):
            try:
                choices, ptok, ctok, _ = self._call_once(
                    messages, temperature, n, seed)
                last_err = None
                break
            except Exception as e:
                last_err = str(e)
                wait = min(2 ** attempt, 30) + random.uniform(0, 1)
                logger.warning("attempt %d failed: %s; sleeping %.1fs",
                               attempt + 1, e, wait)
                time.sleep(wait)
        if last_err is not None:
            raise RuntimeError(
                f"LLM call failed after 5 retries: {last_err}"
            )

        latency = time.time() - t0
        result = {
            "choices": choices,
            "prompt_tokens": ptok,
            "completion_tokens": ctok,
            "latency_s": latency,
            "temperature": temperature,
            "n": n,
        }
        self._cache_put(key, result)
        return result

    def generate_batch(
        self,
        jobs: List[Dict[str, Any]],
        exemplars: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Run many generations in a thread pool.

        Each job: {schema, question, evidence, temperature, n, seed, extra_note}
        Returns results aligned with jobs order.
        """
        results: List[Optional[Dict[str, Any]]] = [None] * len(jobs)

        def _run(i, job):
            return i, self.generate(
                schema=job["schema"],
                question=job["question"],
                evidence=job.get("evidence", ""),
                temperature=job.get("temperature", 0.0),
                n=job.get("n", 1),
                seed=job.get("seed", 0),
                exemplars=exemplars,
                extra_note=job.get("extra_note", ""),
            )

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futs = [pool.submit(_run, i, j) for i, j in enumerate(jobs)]
            for f in as_completed(futs):
                i, res = f.result()
                results[i] = res
        return results  # type: ignore
