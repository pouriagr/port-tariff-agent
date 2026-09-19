"""The README block helpers: finding a block, and comparing tables without fighting spaces."""

from __future__ import annotations

import pytest

from tests.report import BlockNotFoundError, extract_block, replacement_message, rows_of

START = "<!-- x:start -->"
END = "<!-- x:end -->"

DOCUMENT = f"""# Title

before

{START}
| A | B |
|---|--:|
| 1 | 2 |
{END}

after
"""


class TestExtractBlock:
    def test_it_returns_what_is_between_the_markers(self) -> None:
        block = extract_block(DOCUMENT, START, END)

        assert block.startswith("| A | B |")
        assert "before" not in block
        assert "after" not in block

    def test_a_missing_marker_names_the_block(self) -> None:
        with pytest.raises(BlockNotFoundError, match="x:start"):
            extract_block("# Title\n", START, END)

    def test_an_end_before_the_start_is_not_a_block(self) -> None:
        with pytest.raises(BlockNotFoundError):
            extract_block(f"{END}\n{START}\n", START, END)


class TestRowsOf:
    def test_spacing_does_not_change_a_row(self) -> None:
        tidy = "| A | B |\n|---|---|\n| 1 | 2 |"
        untidy = "|A|B|\n|:--|--:|\n|   1   |2|"

        assert rows_of(tidy) == rows_of(untidy)

    def test_the_alignment_row_is_dropped(self) -> None:
        assert rows_of("| A |\n|---|\n| 1 |") == [("A",), ("1",)]

    def test_prose_around_the_table_is_ignored(self) -> None:
        block = "Recorded yesterday.\n\n| A |\n|---|\n| 1 |\n\nAlso: none."

        assert rows_of(block) == [("A",), ("1",)]

    def test_a_different_number_changes_the_rows(self) -> None:
        """The whole point: an edited figure must not compare equal."""
        assert rows_of("| A |\n|---|\n| 1 |") != rows_of("| A |\n|---|\n| 2 |")


def test_the_failure_message_carries_the_replacement() -> None:
    message = replacement_message("accuracy", "| A |\n|---|\n| 1 |")

    assert "out of date" in message
    assert "| A |" in message
