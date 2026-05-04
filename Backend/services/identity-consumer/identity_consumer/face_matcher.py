"""Face embedding comparison using cosine similarity.

For pgvector queries (production), use SQL: 1 - (embedding <=> query_embedding)
For in-memory comparison (unit tests, quick checks), use numpy.
"""

from __future__ import annotations

import enum

import numpy as np


class MatchBand(str, enum.Enum):
    ACCEPT = "accept"
    UNCERTAIN = "uncertain"


class FaceMatcher:
    def __init__(
        self,
        similarity_threshold: float | None = None,
        accept_threshold: float = 0.65,
        uncertain_threshold: float = 0.50,
        margin: float = 0.08,
    ):
        if similarity_threshold is not None:
            self.uncertain_threshold = similarity_threshold
            self.accept_threshold = max(accept_threshold, similarity_threshold + 0.10)
        else:
            self.uncertain_threshold = uncertain_threshold
            self.accept_threshold = accept_threshold
        self.margin = margin
        self.threshold = self.uncertain_threshold

    @staticmethod
    def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

    def find_top_matches(
        self,
        query: np.ndarray,
        templates: dict[str, list[np.ndarray]],
        top_k: int = 5,
        *,
        accept_threshold: float | None = None,
        uncertain_threshold: float | None = None,
        margin: float | None = None,
    ) -> list[dict]:
        """Find top-k matching employees from template dict.

        templates: {employee_id: [embedding1, embedding2, ...]}
        Returns: [{employee_id, score, template_idx, band}, ...] sorted by score desc
        """
        accept_t = accept_threshold if accept_threshold is not None else self.accept_threshold
        uncertain_t = uncertain_threshold if uncertain_threshold is not None else self.uncertain_threshold
        margin_t = margin if margin is not None else self.margin

        results = []
        query_norm = query / (np.linalg.norm(query) + 1e-8)

        for emp_id, embs in templates.items():
            best_score = 0.0
            best_idx = 0
            for idx, emb in enumerate(embs):
                emb_norm = emb / (np.linalg.norm(emb) + 1e-8)
                score = float(np.dot(query_norm, emb_norm))
                if score > best_score:
                    best_score = score
                    best_idx = idx
            if best_score >= uncertain_t:
                band = MatchBand.ACCEPT if best_score >= accept_t else MatchBand.UNCERTAIN
                results.append({
                    "employee_id": emp_id,
                    "score": best_score,
                    "template_idx": best_idx,
                    "band": band,
                })

        results.sort(key=lambda x: x["score"], reverse=True)

        # Margin test: if top-2 scores differ by less than margin, demote best
        if len(results) >= 2:
            gap = results[0]["score"] - results[1]["score"]
            if gap < margin_t:
                results[0]["band"] = MatchBand.UNCERTAIN

        return results[:top_k]
