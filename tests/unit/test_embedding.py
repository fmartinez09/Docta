import pytest
from tests.embedder_fakes import FakeEmbedder

from docta_api.embedding import (
    MAX_EMBED_MODEL_LENGTH,
    MAX_EMBEDDING_DIMENSIONS,
    MAX_EMBEDDING_VERSION_LENGTH,
    EmbeddingInvalid,
    EmbeddingUnavailable,
    derive_embedding_version,
)


def test_version_has_model_dimensions_and_short_hash() -> None:
    model, dims, digest = derive_embedding_version(model="vendor/m", dimensions=4096).split("|")

    assert (model, dims) == ("vendor/m", "4096")
    assert len(digest) == 8
    int(digest, 16)


def test_version_is_deterministic() -> None:
    assert derive_embedding_version(model="m", dimensions=8) == derive_embedding_version(
        model="m", dimensions=8
    )


@pytest.mark.parametrize(
    "change",
    [
        {"query_template": "query: {text}"},
        {"document_template": "passage: {text}"},
        {"input_type": "nvidia"},
        {"provider_routing": {"order": ["a"], "allow_fallbacks": False}},
        {"model": "other"},
        {"dimensions": 9},
    ],
)
def test_every_input_changes_the_version(change: dict) -> None:
    base = {"model": "m", "dimensions": 8}

    assert derive_embedding_version(**base) != derive_embedding_version(**(base | change))


def test_routing_key_order_does_not_change_the_version() -> None:
    first = derive_embedding_version(
        model="m", dimensions=8, provider_routing={"a": 1, "b": 2}
    )
    second = derive_embedding_version(
        model="m", dimensions=8, provider_routing={"b": 2, "a": 1}
    )

    assert first == second


def test_longest_allowed_model_fits_the_stored_column() -> None:
    version = derive_embedding_version(
        model="x" * MAX_EMBED_MODEL_LENGTH, dimensions=MAX_EMBEDDING_DIMENSIONS
    )

    assert len(version) <= MAX_EMBEDDING_VERSION_LENGTH


def test_overlong_version_is_rejected() -> None:
    with pytest.raises(ValueError):
        derive_embedding_version(model="x" * 300, dimensions=8)


def test_fake_embedder_is_deterministic_and_normalized() -> None:
    fake = FakeEmbedder(dimensions=6)

    assert fake.embed_query("a") == fake.embed_query("a")
    assert fake.embed_query("a") != fake.embed_query("b")
    assert fake.embed_documents(["a"])[0] == fake.embed_query("a")
    assert len(fake.embed_query("a")) == 6
    assert sum(value * value for value in fake.embed_query("a")) == pytest.approx(1.0)
    assert fake.version == derive_embedding_version(model="fake-embedder", dimensions=6)


def test_fake_embedder_injects_failures_and_bad_shapes() -> None:
    flaky = FakeEmbedder(fail_with=EmbeddingUnavailable("down"), fail_calls=1)
    with pytest.raises(EmbeddingUnavailable):
        flaky.embed_documents(["a"])
    assert len(flaky.embed_documents(["a"])) == 1

    assert len(FakeEmbedder(dimensions=4, returned_dimensions=3).embed_query("a")) == 3
    assert len(FakeEmbedder(returned_count_delta=1).embed_documents(["a", "b"])) == 3
    assert len(FakeEmbedder(returned_count_delta=-1).embed_documents(["a", "b"])) == 1
    with pytest.raises(EmbeddingInvalid):
        FakeEmbedder(fail_with=EmbeddingInvalid("bad")).embed_query("a")
