from __future__ import annotations

from datetime import UTC, datetime
import os
from pathlib import Path

import pytest

from video_factory.domain import HashDigest, OpaqueId
from video_factory.mutation import ContentObject, PathNodeKind, WorkspaceTrustState
from video_factory.mutation.trust import workspace_observation_sha256
from video_factory_runtime import (
    ContentAddressedFixtureStore,
    FixtureRuntimeBoundary,
    FixtureWorkspaceObserver,
    RuntimeFilesystemError,
    read_stable_regular_file,
)
import video_factory_runtime.filesystem as runtime_filesystem


def _boundary(tmp_path: Path) -> FixtureRuntimeBoundary:
    return FixtureRuntimeBoundary.initialize(
        tmp_path / "fixture-runtime",
        runtime_id="runtime-filesystem-test",
        fixture_only=True,
    )


def test_workspace_observation_is_complete_stable_and_byte_bound(tmp_path: Path) -> None:
    observer = FixtureWorkspaceObserver(
        _boundary(tmp_path), workspace_id=OpaqueId("workspace-fixture")
    )
    (observer.root / "artifacts").mkdir()
    (observer.root / "artifacts" / "input.json").write_bytes(b'{"value":1}')

    first = observer.observe(revision_id=OpaqueId("revision-a"))
    second = observer.observe(revision_id=OpaqueId("revision-a"))

    assert first == second
    assert first.complete is True
    assert first.trust_state is WorkspaceTrustState.TRUSTED
    assert tuple((str(item.path), item.node_kind) for item in first.entries) == (
        ("artifacts", PathNodeKind.DIRECTORY),
        ("artifacts/input.json", PathNodeKind.FILE),
    )
    assert workspace_observation_sha256(first) == workspace_observation_sha256(second)

    (observer.root / "artifacts" / "input.json").write_bytes(b'{"value":2}')
    changed = observer.observe(revision_id=OpaqueId("revision-a"))
    assert changed.manifest_sha256 != first.manifest_sha256
    assert changed.entries != first.entries


def test_workspace_observer_records_link_without_following_it(tmp_path: Path) -> None:
    observer = FixtureWorkspaceObserver(
        _boundary(tmp_path), workspace_id=OpaqueId("workspace-links")
    )
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside")
    link = observer.root / "link.txt"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("the test environment cannot create symlinks")

    observation = observer.observe(revision_id=OpaqueId("revision-links"))

    assert observation.trust_state is WorkspaceTrustState.UNTRUSTED
    assert len(observation.entries) == 1
    assert observation.entries[0].node_kind is PathNodeKind.SYMLINK
    assert observation.entries[0].exact_sha256 is None


def test_content_store_resolves_exact_immutable_bytes(tmp_path: Path) -> None:
    boundary = _boundary(tmp_path)
    store = ContentAddressedFixtureStore(boundary)
    observed = store.put(object_id=OpaqueId("content-a"), payload=b"new bytes")
    content = ContentObject(
        object_id=observed.object_id,
        exact_sha256=observed.exact_sha256,
        byte_length=observed.byte_length,
    )

    resolved = store.resolve_current(content, evaluated_at=datetime.now(UTC))

    assert resolved == observed
    assert store.read_current(content) == b"new bytes"
    assert resolved is not None
    assert resolved.resolver_evidence.sha256 == observed.resolver_evidence.sha256

    object_path = store.objects / f"{content.exact_sha256}.bin"
    object_path.write_bytes(b"tampered")
    assert store.resolve_current(content, evaluated_at=datetime.now(UTC)) is None
    with pytest.raises(RuntimeFilesystemError) as caught:
        store.read_current(content)
    assert caught.value.reason_code == "runtime.content.mismatch"


def test_managed_target_rejects_non_directory_ancestor(tmp_path: Path) -> None:
    observer = FixtureWorkspaceObserver(
        _boundary(tmp_path), workspace_id=OpaqueId("workspace-ancestor")
    )
    (observer.root / "artifacts").write_bytes(b"not a directory")

    with pytest.raises(RuntimeFilesystemError) as caught:
        observer.require_absent("artifacts/new.json")

    assert caught.value.reason_code == "runtime.filesystem.ancestor_type"


def test_stable_reader_detects_change_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "large.bin"
    target.write_bytes(b"a" * (2 * 1024 * 1024))
    original_read = runtime_filesystem.os.read
    changed = False

    def changing_read(descriptor: int, size: int) -> bytes:
        nonlocal changed
        payload = original_read(descriptor, size)
        if payload and not changed:
            changed = True
            try:
                with target.open("ab") as stream:
                    stream.write(b"b")
                    stream.flush()
                    os.fsync(stream.fileno())
            except PermissionError:
                pytest.skip("platform denies a concurrent test write")
        return payload

    monkeypatch.setattr(runtime_filesystem.os, "read", changing_read)
    with pytest.raises(RuntimeFilesystemError) as caught:
        read_stable_regular_file(target)
    assert caught.value.reason_code in {
        "runtime.filesystem.bytes_changed",
        "runtime.filesystem.path_changed",
    }


def test_content_lookup_requires_aware_time(tmp_path: Path) -> None:
    store = ContentAddressedFixtureStore(_boundary(tmp_path))
    observed = store.put(object_id=OpaqueId("content-time"), payload=b"bytes")
    content = ContentObject(
        object_id=observed.object_id,
        exact_sha256=HashDigest(str(observed.exact_sha256)),
        byte_length=observed.byte_length,
    )
    with pytest.raises(RuntimeFilesystemError) as caught:
        store.resolve_current(content, evaluated_at=datetime(2026, 8, 10))
    assert caught.value.reason_code == "runtime.content.evaluation_time"
