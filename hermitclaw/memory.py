"""Smallville-inspired memory stream with three-factor retrieval."""

import json
import logging
import math
import os
import re
from datetime import datetime

from hermitclaw.config import config
from hermitclaw.prompts import IMPORTANCE_PROMPT
from hermitclaw.providers import chat_short, embed

logger = logging.getLogger("hermitclaw.memory")

STREAM_FILENAME = "memory_stream.jsonl"


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Pure-Python cosine similarity — no numpy needed."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class MemoryStream:
    """Append-only memory stream with recency × importance × relevance retrieval."""

    def __init__(self, environment_path: str):
        self.path = os.path.join(environment_path, STREAM_FILENAME)
        self.memories: list[dict] = []
        self.importance_sum: float = 0.0  # running sum since last reflection
        self._next_id: int = 0
        self._load()

    def _load(self):
        """Load existing memories from JSONL on startup."""
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    self.memories.append(entry)
        except Exception as e:
            logger.error(f"Failed to load memory stream: {e}")

        if self.memories:
            # Restore next ID from highest existing ID
            max_id = max(int(m["id"].split("_")[1]) for m in self.memories)
            self._next_id = max_id + 1
            # importance_sum starts at 0 after restart (reflection threshold resets)
        logger.info(f"Loaded {len(self.memories)} memories from stream")

    def add(
        self,
        content: str,
        kind: str = "thought",
        depth: int = 0,
        references: list[str] | None = None,
    ) -> dict:
        """Score importance, compute embedding, append to stream."""
        # Score importance via LLM
        importance = self._score_importance(content)

        # Compute embedding
        try:
            embedding = embed(content)
        except Exception as e:
            logger.error(f"Embedding failed: {e}")
            embedding = []

        entry = {
            "id": f"m_{self._next_id:04d}",
            "timestamp": datetime.now().isoformat(),
            "kind": kind,
            "content": content,
            "importance": importance,
            "depth": depth,
            "references": references or [],
            "embedding": embedding,
        }

        self.memories.append(entry)
        self._next_id += 1
        self.importance_sum += importance

        # Append to JSONL file
        try:
            with open(self.path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error(f"Failed to write memory: {e}")

        logger.info(f"Memory {entry['id']}: importance={importance}, kind={kind}")
        return entry

    def retrieve(self, query: str, top_k: int = None) -> list[dict]:
        """Three-factor retrieval: recency × importance × relevance."""
        if top_k is None:
            top_k = config.get("memory_retrieval_count", 3)

        if not self.memories:
            return []

        # Embed the query
        try:
            query_embedding = embed(query)
        except Exception as e:
            logger.error(f"Query embedding failed: {e}")
            return self.memories[-top_k:]  # fallback to recent

        decay_rate = config.get("recency_decay_rate", 0.995)
        now = datetime.now()
        scored = []

        for mem in self.memories:
            # Recency score
            try:
                mem_time = datetime.fromisoformat(mem["timestamp"])
                hours_ago = (now - mem_time).total_seconds() / 3600.0
            except Exception:
                hours_ago = 1000.0
            recency = math.exp(-(1 - decay_rate) * hours_ago)

            # Importance score (normalized 0-1)
            importance = mem["importance"] / 10.0

            # Relevance score (cosine similarity, already 0-1 range for normalized vectors)
            if mem.get("embedding") and query_embedding:
                relevance = _cosine_sim(query_embedding, mem["embedding"])
            else:
                relevance = 0.0

            score = recency + importance + relevance
            scored.append((score, mem))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [mem for _, mem in scored[:top_k]]

    def should_reflect(self) -> bool:
        """Check if accumulated importance exceeds the reflection threshold."""
        threshold = config.get("reflection_threshold", 50)
        return self.importance_sum >= threshold

    def reset_importance_sum(self):
        """Reset after a reflection cycle."""
        self.importance_sum = 0.0

    def get_recent(self, n: int = 10, kind: str | None = None) -> list[dict]:
        """Get the last N memories, optionally filtered by kind."""
        if kind:
            filtered = [m for m in self.memories if m["kind"] == kind]
            return filtered[-n:]
        return self.memories[-n:]

    def _score_importance(self, content: str) -> int:
        """Ask the LLM to rate importance 1-10."""
        try:
            result = chat_short(
                [{"role": "user", "content": content}],
                instructions=IMPORTANCE_PROMPT,
            )
            # Extract the first integer from the response
            match = re.search(r"\d+", result)
            if match:
                score = int(match.group())
                return max(1, min(10, score))
        except Exception as e:
            logger.error(f"Importance scoring failed: {e}")
        return 5  # default to middle
