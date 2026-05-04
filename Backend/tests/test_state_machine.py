"""Test the identity state machine: unknown -> candidate -> identified -> corrected.

Rules from 2.0-project.md:
- unknown: default for all new persons
- candidate: face evidence suggests employee but not enough confidence
- identified: enough evidence accumulated, auto-promoted
- corrected: operator explicitly changed identity
- One person CANNOT rename from Employee A to Employee B automatically
- Conflicting claims create a review event, not a rename
- Once identified, downgrades are rare and explicit
"""

import uuid
import pytest
from identity_consumer.state_machine import IdentityStateMachine, StateTransition, TransitionDenied


@pytest.fixture
def sm():
    return IdentityStateMachine()


def test_new_person_starts_unknown(sm):
    assert sm.current_state == "unknown"


def test_unknown_to_candidate_on_face_match(sm):
    result = sm.process_face_evidence(
        employee_id=uuid.uuid4(),
        similarity=0.65,
        observation_count=1,
    )
    assert result.new_state == "candidate"
    assert sm.current_state == "candidate"


def test_candidate_requires_minimum_similarity(sm):
    result = sm.process_face_evidence(
        employee_id=uuid.uuid4(),
        similarity=0.3,  # too low
        observation_count=1,
    )
    assert result.new_state == "unknown"  # stays unknown


def test_candidate_to_identified_needs_multiple_observations(sm):
    emp_id = uuid.uuid4()
    # First observation -> candidate
    sm.process_face_evidence(employee_id=emp_id, similarity=0.75, observation_count=1)
    assert sm.current_state == "candidate"

    # Second observation -> still candidate (need 3+)
    sm.process_face_evidence(employee_id=emp_id, similarity=0.78, observation_count=2)
    assert sm.current_state == "candidate"

    # Third observation -> identified
    result = sm.process_face_evidence(employee_id=emp_id, similarity=0.80, observation_count=3)
    assert result.new_state == "identified"
    assert sm.current_state == "identified"


def test_cannot_auto_rename_identified_person(sm):
    emp_a = uuid.uuid4()
    emp_b = uuid.uuid4()

    # Identify as Employee A
    for i in range(3):
        sm.process_face_evidence(employee_id=emp_a, similarity=0.85, observation_count=i + 1)
    assert sm.current_state == "identified"
    assert sm.employee_id == emp_a

    # Try to identify as Employee B -> must NOT rename, should flag conflict
    result = sm.process_face_evidence(employee_id=emp_b, similarity=0.90, observation_count=1)
    assert sm.employee_id == emp_a  # stays as A
    assert result.conflict is True
    assert sm.current_state == "identified"  # stays identified


def test_operator_correction_overrides(sm):
    emp_a = uuid.uuid4()
    emp_b = uuid.uuid4()

    # Auto-identify as A
    for i in range(3):
        sm.process_face_evidence(employee_id=emp_a, similarity=0.85, observation_count=i + 1)
    assert sm.employee_id == emp_a

    # Operator corrects to B
    result = sm.apply_correction(employee_id=emp_b, operator="admin")
    assert result.new_state == "corrected"
    assert sm.employee_id == emp_b
    assert sm.current_state == "corrected"


def test_corrected_state_resists_auto_changes(sm):
    emp_a = uuid.uuid4()
    emp_b = uuid.uuid4()

    # Operator sets to A
    sm.apply_correction(employee_id=emp_a, operator="admin")
    assert sm.current_state == "corrected"

    # Auto-evidence for B should not override
    result = sm.process_face_evidence(employee_id=emp_b, similarity=0.95, observation_count=5)
    assert sm.employee_id == emp_a  # stays corrected
    assert sm.current_state == "corrected"


def test_auto_promotion_requires_consistent_employee(sm):
    emp_a = uuid.uuid4()
    emp_b = uuid.uuid4()

    sm.process_face_evidence(employee_id=emp_a, similarity=0.75, observation_count=1)
    sm.process_face_evidence(employee_id=emp_b, similarity=0.80, observation_count=1)  # different employee!
    sm.process_face_evidence(employee_id=emp_a, similarity=0.78, observation_count=2)

    # Should NOT auto-promote because evidence is inconsistent
    assert sm.current_state == "candidate"
