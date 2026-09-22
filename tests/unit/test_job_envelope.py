from uuid import uuid4

import pytest

from docta_api.jobs import JobEnvelope


def test_versioned_job_envelope_roundtrip_and_rejects_unexpected_payloads():
    envelope = JobEnvelope(
        event_id=uuid4(),
        job_id=uuid4(),
        course_id=uuid4(),
        document_version_id=uuid4(),
        pipeline_version="phase0-pymupdf-v1",
        correlation_id="request-1",
        created_at="2026-09-05T00:00:00+00:00",
    )
    assert JobEnvelope.parse(envelope.fields()) == envelope
    for mutation in (
        {"schema_version": "2"},
        {"prompt": "untrusted"},
        {"correlation_id": "x" * 101},
        {"job_id": "not-a-uuid"},
    ):
        with pytest.raises(ValueError):
            JobEnvelope.parse({**envelope.fields(), **mutation})
    with pytest.raises(ValueError):
        JobEnvelope.parse({"job_id": str(envelope.job_id)})
