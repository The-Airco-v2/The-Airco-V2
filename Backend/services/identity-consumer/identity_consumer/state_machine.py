"""Identity state machine: unknown -> candidate -> identified -> corrected.

Implements the anti-false-positive rules from 2.0-project.md:
- Face recognition is evidence, not truth
- One person cannot auto-rename from Employee A to Employee B
- Conflicting claims create review events, not renames
- Operator corrections take precedence over automatic inference
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class StateTransition:
    old_state: str
    new_state: str
    employee_id: uuid.UUID | None = None
    conflict: bool = False
    reason: str = ""


class TransitionDenied(Exception):
    pass


# Promotion thresholds (configurable)
CANDIDATE_MIN_SIMILARITY = 0.55
IDENTIFIED_MIN_SIMILARITY = 0.70
IDENTIFIED_MIN_OBSERVATIONS = 3
IDENTIFIED_MIN_ACCEPT_OBSERVATIONS = 3


class IdentityStateMachine:
    """State machine for a single canonical session person.

    States: unknown -> candidate -> identified -> corrected
    """

    def __init__(self):
        self._state: str = "unknown"
        self._employee_id: uuid.UUID | None = None
        self._evidence: dict[uuid.UUID, list[float]] = {}  # employee_id -> [similarities]
        self._accept_evidence: dict[uuid.UUID, list[float]] = {}  # accept-band only
        self._total_observations: int = 0

    @property
    def current_state(self) -> str:
        return self._state

    @property
    def employee_id(self) -> uuid.UUID | None:
        return self._employee_id

    def process_face_evidence(
        self,
        employee_id: uuid.UUID,
        similarity: float,
        observation_count: int,
        band: str = "accept",
    ) -> StateTransition:
        """Process a face recognition observation.

        Args:
            band: "accept" or "uncertain". Only accept-band evidence can promote states.

        Returns the resulting state transition (may be a no-op if state unchanged).
        """
        old_state = self._state

        # Corrected state resists automatic changes
        if self._state == "corrected":
            return StateTransition(old_state=old_state, new_state="corrected",
                                   employee_id=self._employee_id)

        # Track all evidence per employee
        if employee_id not in self._evidence:
            self._evidence[employee_id] = []
        self._evidence[employee_id].append(similarity)

        # Track accept-band evidence separately
        if band == "accept":
            if employee_id not in self._accept_evidence:
                self._accept_evidence[employee_id] = []
            self._accept_evidence[employee_id].append(similarity)

        # Uncertain-band evidence is recorded but does not promote
        if band == "uncertain":
            return StateTransition(old_state=old_state, new_state=self._state,
                                   employee_id=self._employee_id)

        # If already identified as different employee -> conflict, don't rename
        if self._state == "identified" and self._employee_id != employee_id:
            return StateTransition(
                old_state=old_state,
                new_state="identified",
                employee_id=self._employee_id,
                conflict=True,
                reason="Conflicting identity: already identified, new claim for different employee",
            )

        # Below minimum similarity -> stay unknown
        if similarity < CANDIDATE_MIN_SIMILARITY:
            return StateTransition(old_state=old_state, new_state=self._state,
                                   employee_id=self._employee_id)

        # Enough for candidate?
        if self._state == "unknown":
            self._state = "candidate"
            self._employee_id = employee_id
            return StateTransition(old_state=old_state, new_state="candidate",
                                   employee_id=employee_id)

        # Check auto-promotion to identified
        if self._state == "candidate":
            # Must be same employee as current candidate
            if self._employee_id != employee_id:
                # Inconsistent evidence - stay candidate, update to strongest claim
                best_emp = max(self._evidence, key=lambda e: sum(self._evidence[e]) / len(self._evidence[e]))
                self._employee_id = best_emp
                return StateTransition(old_state=old_state, new_state="candidate",
                                       employee_id=best_emp)

            # Check promotion criteria using accept-band evidence only
            accept_obs = self._accept_evidence.get(employee_id, [])
            avg_similarity = sum(accept_obs) / len(accept_obs) if accept_obs else 0

            if (len(accept_obs) >= IDENTIFIED_MIN_ACCEPT_OBSERVATIONS
                    and avg_similarity >= IDENTIFIED_MIN_SIMILARITY
                    and self._is_consistent(employee_id)):
                self._state = "identified"
                return StateTransition(old_state=old_state, new_state="identified",
                                       employee_id=employee_id)

            return StateTransition(old_state=old_state, new_state="candidate",
                                   employee_id=employee_id)

        # Already identified as same employee - no change
        return StateTransition(old_state=old_state, new_state=self._state,
                               employee_id=self._employee_id)

    def apply_correction(
        self,
        employee_id: uuid.UUID,
        operator: str,
    ) -> StateTransition:
        """Operator manually corrects identity. Always succeeds."""
        old_state = self._state
        self._state = "corrected"
        self._employee_id = employee_id
        return StateTransition(
            old_state=old_state,
            new_state="corrected",
            employee_id=employee_id,
            reason=f"Operator correction by {operator}",
        )

    def _is_consistent(self, employee_id: uuid.UUID) -> bool:
        """Check that the majority of evidence points to the same employee."""
        if not self._evidence:
            return False
        total = sum(len(v) for v in self._evidence.values())
        emp_count = len(self._evidence.get(employee_id, []))
        return emp_count / total >= 0.6  # At least 60% of evidence is consistent
