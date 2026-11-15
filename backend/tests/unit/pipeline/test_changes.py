"""Change detection: every path lands in exactly one of the five classes."""

from __future__ import annotations

from repoatlas.ingest.inventory import Inventory, build_inventory
from repoatlas.ingest.source import MemorySourceTree
from repoatlas.pipeline.changes import (
    ChangeSet,
    FileState,
    Rename,
    diff_files,
    diff_states,
    inventory_states,
)


def states(**files: str) -> dict[str, FileState]:
    return {path.replace("__", "/"): FileState(blob_oid=oid) for path, oid in files.items()}


def test_identical_snapshots_have_only_unchanged_files() -> None:
    before = {"a.py": FileState(blob_oid="1"), "b/c.py": FileState(blob_oid="2")}
    changes = diff_states(before, dict(before))
    assert changes == ChangeSet(unchanged=["a.py", "b/c.py"])
    assert changes.is_empty
    assert changes.arrived == [] and changes.gone == []


def test_a_new_blob_at_the_same_path_is_modified() -> None:
    changes = diff_states({"a.py": FileState(blob_oid="1")}, {"a.py": FileState(blob_oid="2")})
    assert changes.modified == ["a.py"]
    assert changes.arrived == ["a.py"] and changes.gone == []
    assert not changes.is_empty


def test_added_and_deleted_paths() -> None:
    changes = diff_states(
        {"keep.py": FileState(blob_oid="1"), "old.py": FileState(blob_oid="2")},
        {"keep.py": FileState(blob_oid="1"), "new.py": FileState(blob_oid="3")},
    )
    assert changes.unchanged == ["keep.py"]
    assert changes.added == ["new.py"]
    assert changes.deleted == ["old.py"]
    assert changes.renamed == []


def test_the_same_blob_at_a_new_path_is_a_rename() -> None:
    changes = diff_states(
        {"src/old.py": FileState(blob_oid="1")}, {"src/new.py": FileState(blob_oid="1")}
    )
    assert changes.renamed == [Rename(old="src/old.py", new="src/new.py")]
    assert changes.added == [] and changes.deleted == []
    # For what is built from a file, a rename is a delete and an add.
    assert changes.gone == ["src/old.py"]
    assert changes.arrived == ["src/new.py"]


def test_a_copy_of_a_file_that_stays_is_added_not_renamed() -> None:
    changes = diff_states(
        {"a.py": FileState(blob_oid="1")},
        {"a.py": FileState(blob_oid="1"), "copy.py": FileState(blob_oid="1")},
    )
    assert changes.unchanged == ["a.py"]
    assert changes.added == ["copy.py"]
    assert changes.renamed == []


def test_several_files_with_one_content_pair_up_in_path_order() -> None:
    changes = diff_states(
        {"a/__init__.py": FileState(blob_oid="e"), "b/__init__.py": FileState(blob_oid="e")},
        {
            "x/__init__.py": FileState(blob_oid="e"),
            "y/__init__.py": FileState(blob_oid="e"),
            "z/__init__.py": FileState(blob_oid="e"),
        },
    )
    assert changes.renamed == [
        Rename(old="a/__init__.py", new="x/__init__.py"),
        Rename(old="b/__init__.py", new="y/__init__.py"),
    ]
    assert changes.added == ["z/__init__.py"]
    assert changes.deleted == []


def test_a_rename_with_an_edit_is_a_delete_and_an_add() -> None:
    changes = diff_states({"old.py": FileState(blob_oid="1")}, {"new.py": FileState(blob_oid="2")})
    assert changes.deleted == ["old.py"] and changes.added == ["new.py"]


def test_a_file_that_becomes_skipped_is_modified() -> None:
    changes = diff_states(
        {"big.py": FileState(blob_oid="1")},
        {"big.py": FileState(blob_oid="1", skip_reason="too_large")},
    )
    assert changes.modified == ["big.py"]


def test_every_path_is_in_exactly_one_class() -> None:
    before = states(same="1", edited="2", removed="3", moved__from="4")
    after = states(same="1", edited="20", fresh="5", moved__to="4")
    changes = diff_states(before, after)
    renamed_old = [rename.old for rename in changes.renamed]
    renamed_new = [rename.new for rename in changes.renamed]
    old_side = [*changes.unchanged, *changes.modified, *changes.deleted, *renamed_old]
    new_side = [*changes.unchanged, *changes.modified, *changes.added, *renamed_new]
    assert sorted(old_side) == sorted(before)
    assert sorted(new_side) == sorted(after)
    assert changes.counts() == {
        "unchanged": 1,
        "modified": 1,
        "added": 1,
        "deleted": 1,
        "renamed": 1,
    }


def test_diff_files_reads_an_inventory() -> None:
    def inventory(files: dict[str, bytes | tuple[str, bytes]]) -> Inventory:
        return build_inventory(MemorySourceTree(files), max_source_files=100, max_file_bytes=10_000)

    old = inventory({"pkg/a.py": b"x = 1\n", "pkg/b.py": b"y = 2\n", "pkg/gone.py": b"z = 3\n"})
    new = inventory({"pkg/a.py": b"x = 1\n", "pkg/b.py": b"y = 20\n", "pkg/moved.py": b"z = 3\n"})
    changes = diff_files(inventory_states(old.files), new)
    assert changes.unchanged == ["pkg/a.py"]
    assert changes.modified == ["pkg/b.py"]
    assert changes.renamed == [Rename(old="pkg/gone.py", new="pkg/moved.py")]
