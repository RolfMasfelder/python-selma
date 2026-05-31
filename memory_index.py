# ============================================================
# memory_index.py
#
# SQLite FTS5 index for Selma's memory files.
# Covers MEMORY.md and memory/YYYY-MM-DD.md daily notes.
#
# Phase 2: FTS5 full-text search (always active)
# Phase 3: Hybrid search = FTS5 + cosine similarity via Ollama
#          embeddings. Enabled via selma.json:
#            "memory": { "vector_search": true, "embed_model": "nomic-embed-text" }
#
# Design: sync-on-demand (lazy sync). The index is built
# the first time memory_search is called. Only files whose
# SHA-256 hash has changed are re-indexed.
#
# Database location: <workspace_dir>/../memory.db
#   → .selma/memory.db  (outside workspace, not agent-visible)
# ============================================================

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sqlite3
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 500   # max characters per FTS chunk
_HYBRID_VECTOR_WEIGHT = 0.5
_HYBRID_TEXT_WEIGHT   = 0.5
_DECAY_WEIGHT         = 0.3   # how strongly temporal decay affects the score


# ════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ════════════════════════════════════════════════════════════

@dataclass
class SearchResult:
    path: str       # workspace-relative path, e.g. "MEMORY.md"
    content: str    # chunk text
    score: float    # normalised score 0..1 (higher = more relevant)


# ════════════════════════════════════════════════════════════
# EMBEDDING PROVIDER
# ════════════════════════════════════════════════════════════

class EmbeddingProvider:
    """
    Calls Ollama's OpenAI-compatible embeddings endpoint.
    Returns None on any error so callers can degrade gracefully.
    """

    def __init__(self, model: str, base_url: str):
        self._model = model
        self._base_url = base_url.rstrip("/")

    def embed(self, text: str) -> list[float] | None:
        url = f"{self._base_url}/embeddings"
        payload = json.dumps({"model": self._model, "input": text}).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                return data["data"][0]["embedding"]
        except Exception as e:
            logger.warning("Embedding failed | model=%s error=%s", self._model, e)
            return None


# ════════════════════════════════════════════════════════════
# MEMORY INDEX
# ════════════════════════════════════════════════════════════

class MemoryIndex:
    """
    Manages the SQLite FTS5 (+ optional vector) index for all
    memory files in the workspace.

    Usage:
        index = MemoryIndex(workspace_dir=".selma/workspace")
        index.sync()
        results = index.search("important decision")

    With vector search + temporal decay enabled:
        index = MemoryIndex(
            workspace_dir=".selma/workspace",
            vector_search=True,
            embed_model="nomic-embed-text",
            embed_base_url="http://localhost:11434/v1",
            temporal_decay=True,
            temporal_decay_rate=0.01,
        )
    """

    def __init__(
        self,
        workspace_dir: str | Path,
        *,
        vector_search: bool = False,
        embed_model: str = "nomic-embed-text",
        embed_base_url: str = "http://localhost:11434/v1",
        temporal_decay: bool = False,
        temporal_decay_rate: float = 0.01,
    ):
        self._workspace = Path(workspace_dir).resolve()
        db_path = self._workspace.parent / "memory.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._vector_search = vector_search
        self._embedder = (
            EmbeddingProvider(model=embed_model, base_url=embed_base_url)
            if vector_search
            else None
        )
        self._temporal_decay = temporal_decay
        self._decay_rate = temporal_decay_rate

    # ── Schema ───────────────────────────────────────────────

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    path  TEXT PRIMARY KEY,
                    hash  TEXT NOT NULL,
                    mtime REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
                USING fts5(path UNINDEXED, content, tokenize='unicode61')
            """)
            if self._vector_search:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS chunks_vec (
                        path      TEXT    NOT NULL,
                        chunk_idx INTEGER NOT NULL,
                        embedding BLOB    NOT NULL,
                        PRIMARY KEY (path, chunk_idx)
                    )
                """)

    # ── Sync ─────────────────────────────────────────────────

    def sync(self) -> int:
        """
        Hash-based lazy sync: only re-indexes files that have changed.
        Removes index entries for deleted files.
        Returns the number of files actually re-indexed.
        """
        self.ensure_schema()

        disk_files = self._find_memory_files()
        disk_paths = {self._rel(p) for p in disk_files}

        reindexed = 0

        with self._connect() as conn:
            # Remove index entries for files deleted from disk
            stored_paths = {row[0] for row in conn.execute("SELECT path FROM files")}
            for removed in stored_paths - disk_paths:
                conn.execute("DELETE FROM chunks_fts WHERE path = ?", (removed,))
                if self._vector_search:
                    conn.execute("DELETE FROM chunks_vec WHERE path = ?", (removed,))
                conn.execute("DELETE FROM files WHERE path = ?", (removed,))
                logger.debug("Memory index: removed | path=%s", removed)

            # Index new or changed files
            for abs_path in disk_files:
                rel = self._rel(abs_path)
                new_hash = self._hash(abs_path)

                row = conn.execute(
                    "SELECT hash FROM files WHERE path = ?", (rel,)
                ).fetchone()

                if row and row[0] == new_hash:
                    continue    # unchanged — skip

                content = abs_path.read_text(encoding="utf-8", errors="replace")
                chunks = _chunk_text(content)

                conn.execute("DELETE FROM chunks_fts WHERE path = ?", (rel,))
                conn.executemany(
                    "INSERT INTO chunks_fts(path, content) VALUES (?, ?)",
                    [(rel, chunk) for chunk in chunks],
                )

                if self._vector_search and self._embedder:
                    conn.execute("DELETE FROM chunks_vec WHERE path = ?", (rel,))
                    vec_rows: list[tuple] = []
                    for idx, chunk in enumerate(chunks):
                        vec = self._embedder.embed(chunk)
                        if vec is not None:
                            vec_rows.append((rel, idx, json.dumps(vec).encode()))
                    if vec_rows:
                        conn.executemany(
                            "INSERT INTO chunks_vec(path, chunk_idx, embedding) VALUES (?, ?, ?)",
                            vec_rows,
                        )
                    logger.info(
                        "Memory index: embeddings | path=%s chunks=%d embedded=%d",
                        rel, len(chunks), len(vec_rows),
                    )

                conn.execute(
                    "INSERT OR REPLACE INTO files(path, hash, mtime) VALUES (?, ?, ?)",
                    (rel, new_hash, abs_path.stat().st_mtime),
                )
                reindexed += 1
                logger.info("Memory index: re-indexed | path=%s chunks=%d", rel, len(chunks))

        logger.debug("Memory sync complete | reindexed=%d total=%d", reindexed, len(disk_files))
        return reindexed

    # ── Search ───────────────────────────────────────────────

    def search(
        self,
        query: str,
        max_results: int = 10,
        min_score: float | None = None,
    ) -> list[SearchResult]:
        """
        Full-text search over all indexed memory chunks.

        When vector_search=True and embeddings are available:
          hybrid score = 0.5 × BM25_normalised + 0.5 × cosine_similarity

        When temporal_decay=True:
          final_score = 0.7 × base_score + 0.3 × exp(-rate × age_days)

        Otherwise: FTS5 BM25 only.
        """
        self.ensure_schema()

        fts_query = _build_fts_query(query)
        if not fts_query:
            return []

        mtime_by_path = self._load_mtimes() if self._temporal_decay else {}

        if self._vector_search and self._embedder:
            return self._hybrid_search(query, fts_query, max_results, min_score, mtime_by_path)
        return self._fts_search(fts_query, max_results, min_score, mtime_by_path)

    # ── FTS-only search ───────────────────────────────────────

    def _fts_search(
        self,
        fts_query: str,
        max_results: int,
        min_score: float | None,
        mtime_by_path: dict[str, float],
    ) -> list[SearchResult]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT path, content, bm25(chunks_fts) AS score
                    FROM   chunks_fts
                    WHERE  chunks_fts MATCH ?
                    ORDER  BY score
                    LIMIT  ?
                    """,
                    (fts_query, max_results * 3 if self._temporal_decay else max_results),
                ).fetchall()
        except sqlite3.OperationalError as e:
            logger.warning("Memory search failed | query=%r error=%s", fts_query, e)
            return []

        results: list[SearchResult] = []
        for row in rows:
            score = self._apply_decay(_normalise_bm25(row["score"]), row["path"], mtime_by_path)
            if min_score is not None and score < min_score:
                continue
            results.append(SearchResult(
                path=row["path"],
                content=row["content"],
                score=score,
            ))

        if self._temporal_decay:
            results.sort(key=lambda r: r.score, reverse=True)
            results = results[:max_results]

        return results

    # ── Hybrid search ─────────────────────────────────────────

    def _hybrid_search(
        self,
        query: str,
        fts_query: str,
        max_results: int,
        min_score: float | None,
        mtime_by_path: dict[str, float],
    ) -> list[SearchResult]:
        """
        FTS5 candidates → re-rank with cosine similarity → hybrid score.
        Falls back to FTS-only if query embedding fails.
        """
        assert self._embedder is not None

        # Get a larger FTS candidate pool for re-ranking
        candidate_limit = max(max_results * 3, 30)
        try:
            with self._connect() as conn:
                fts_rows = conn.execute(
                    """
                    SELECT path, content, bm25(chunks_fts) AS score
                    FROM   chunks_fts
                    WHERE  chunks_fts MATCH ?
                    ORDER  BY score
                    LIMIT  ?
                    """,
                    (fts_query, candidate_limit),
                ).fetchall()
        except sqlite3.OperationalError as e:
            logger.warning("Hybrid FTS stage failed | query=%r error=%s", fts_query, e)
            return []

        if not fts_rows:
            return []

        # Query embedding
        query_vec = self._embedder.embed(query)
        if query_vec is None:
            logger.warning("Query embedding failed — falling back to FTS-only")
            return self._fts_search(fts_query, max_results, min_score)

        # Load stored embeddings for candidates
        candidate_paths = list({row["path"] for row in fts_rows})
        stored: dict[tuple[str, int], list[float]] = {}
        try:
            with self._connect() as conn:
                placeholders = ",".join("?" * len(candidate_paths))
                vec_rows = conn.execute(
                    f"SELECT path, chunk_idx, embedding FROM chunks_vec WHERE path IN ({placeholders})",
                    candidate_paths,
                ).fetchall()
            for vr in vec_rows:
                stored[(vr["path"], vr["chunk_idx"])] = json.loads(vr["embedding"])
        except Exception as e:
            logger.warning("Loading embeddings failed | error=%s", e)

        # Build chunk_idx lookup: (path, content) → chunk_idx
        # We match FTS rows to their stored vector by path + position
        # Use a per-path counter since FTS returns rows in order
        path_counters: dict[str, int] = {}

        results: list[SearchResult] = []
        for row in fts_rows:
            path = row["path"]
            bm25_norm = _normalise_bm25(row["score"])

            idx = path_counters.get(path, 0)
            path_counters[path] = idx + 1

            chunk_vec = stored.get((path, idx))
            if chunk_vec is not None:
                cos = _cosine_sim(query_vec, chunk_vec)
                base_score = _HYBRID_VECTOR_WEIGHT * cos + _HYBRID_TEXT_WEIGHT * bm25_norm
            else:
                base_score = bm25_norm

            score = self._apply_decay(base_score, path, mtime_by_path)
            if min_score is not None and score < min_score:
                continue
            results.append(SearchResult(path=path, content=row["content"], score=score))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:max_results]

    # ── Internals ────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _find_memory_files(self) -> list[Path]:
        """Returns MEMORY.md + memory/*.md, in stable order."""
        files: list[Path] = []
        root_mem = self._workspace / "MEMORY.md"
        if root_mem.exists():
            files.append(root_mem)
        mem_dir = self._workspace / "memory"
        if mem_dir.is_dir():
            files.extend(sorted(mem_dir.glob("*.md")))
        return files

    def _rel(self, path: Path) -> str:
        return str(path.relative_to(self._workspace))

    @staticmethod
    def _hash(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _load_mtimes(self) -> dict[str, float]:
        """Returns {rel_path: mtime} for all indexed files."""
        try:
            with self._connect() as conn:
                rows = conn.execute("SELECT path, mtime FROM files").fetchall()
            return {row["path"]: row["mtime"] for row in rows}
        except Exception:
            return {}

    def _apply_decay(self, score: float, path: str, mtime_by_path: dict[str, float]) -> float:
        """
        Temporal decay: recent content scores higher, older content lower.
        final = (1 - w) × score + w × exp(-rate × age_days)
        With w=0.3: new content unchanged, 100d-old content loses ~15 points.
        """
        if not self._temporal_decay:
            return score
        mtime = mtime_by_path.get(path)
        if mtime is None:
            return score
        age_days = max(0.0, (time.time() - mtime) / 86400.0)
        decay = math.exp(-self._decay_rate * age_days)
        return (1.0 - _DECAY_WEIGHT) * score + _DECAY_WEIGHT * decay


# ════════════════════════════════════════════════════════════
# TEXT HELPERS
# ════════════════════════════════════════════════════════════

def _chunk_text(text: str) -> list[str]:
    """
    Splits text into chunks of at most _CHUNK_SIZE characters,
    breaking at paragraph boundaries (double newline).
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        if current and current_len + len(para) > _CHUNK_SIZE:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(para)
        current_len += len(para) + 2

    if current:
        chunks.append("\n\n".join(current))

    return chunks


def _build_fts_query(query: str) -> str:
    """
    Tokenizes query into FTS5 AND-expression.
    "python agent" → '"python" AND "agent"'
    Returns empty string if no usable tokens.
    """
    words = re.findall(r'\w+', query, re.UNICODE)
    if not words:
        return ""
    return " AND ".join(f'"{w}"' for w in words)


def _normalise_bm25(raw_score: float) -> float:
    """Maps a raw BM25 score (negative, lower = better) to [0, 1]."""
    return -raw_score / (1.0 + (-raw_score))


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity in pure Python, clamped to [0, 1]."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))


# ════════════════════════════════════════════════════════════
# MODULE-LEVEL CACHE
# ════════════════════════════════════════════════════════════

_index_cache: dict[str, MemoryIndex] = {}


def get_memory_index(
    workspace_dir: str,
    *,
    vector_search: bool = False,
    embed_model: str = "nomic-embed-text",
    embed_base_url: str = "http://localhost:11434/v1",
    temporal_decay: bool = False,
    temporal_decay_rate: float = 0.01,
) -> MemoryIndex:
    """Returns a cached MemoryIndex instance for the given workspace."""
    key = (
        f"{Path(workspace_dir).resolve()}"
        f"|vs={vector_search}|em={embed_model}"
        f"|td={temporal_decay}|tdr={temporal_decay_rate}"
    )
    if key not in _index_cache:
        _index_cache[key] = MemoryIndex(
            workspace_dir,
            vector_search=vector_search,
            embed_model=embed_model,
            embed_base_url=embed_base_url,
            temporal_decay=temporal_decay,
            temporal_decay_rate=temporal_decay_rate,
        )
    return _index_cache[key]
