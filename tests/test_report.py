"""The README block helpers: finding a block, and comparing one without fighting spaces."""

from __future__ import annotations

import textwrap

import pytest

from tests.report import BlockNotFoundError, extract_block, normalise, replacement_message

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

CAPTION = (
    "Recorded 2026-09-20 against `gemini-3.8-flash` (agent) and `gemini-3.8-flash`"
    " (charge selection), prompt `tariff_agent` v1 sha `90d1bdfabb59`, document"
    " `31471bc01ffe`. 10 model calls, 177,541 prompt and 3,608 output tokens."
)

BLOCK = f"{CAPTION}\n\n| A |\n|---|\n| 1 |\n\nAlso priced, beyond the reference table: none."


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


class TestTables:
    def test_spacing_does_not_change_a_row(self) -> None:
        tidy = "| A | B |\n|---|---|\n| 1 | 2 |"
        untidy = "|A|B|\n|:--|--:|\n|   1   |2|"

        assert normalise(tidy) == normalise(untidy)

    def test_the_alignment_row_is_dropped(self) -> None:
        assert normalise("| A |\n|---|\n| 1 |") == [("A",), ("1",)]

    def test_a_different_number_changes_the_rows(self) -> None:
        """The whole point: an edited figure must not compare equal."""
        assert normalise("| A |\n|---|\n| 1 |") != normalise("| A |\n|---|\n| 2 |")


class TestProse:
    def test_prose_around_the_table_is_compared(self) -> None:
        """The caption is the audit trail; a block that loses it is not the same block."""
        assert normalise(BLOCK) != normalise(BLOCK.replace(CAPTION, ""))

    @pytest.mark.parametrize(
        ("was", "now"),
        [
            pytest.param("2026-09-20", "2026-09-21", id="recording date"),
            pytest.param("`gemini-3.8-flash` (agent)", "`gemini-4-pro` (agent)", id="agent model"),
            pytest.param(
                "`gemini-3.8-flash` (charge selection)",
                "`gemini-4-pro` (charge selection)",
                id="extract model",
            ),
            pytest.param("v1 sha", "v2 sha", id="prompt version"),
            pytest.param("90d1bdfabb59", "0123456789ab", id="prompt sha"),
            pytest.param("31471bc01ffe", "ba0123456789", id="document hash"),
            pytest.param("10 model calls", "11 model calls", id="model calls"),
            pytest.param("177,541 prompt", "177,542 prompt", id="prompt tokens"),
            pytest.param("3,608 output", "3,609 output", id="output tokens"),
        ],
    )
    def test_every_field_of_the_caption_is_guarded(self, was: str, now: str) -> None:
        """Each of these moves when the cassette is re-recorded, and the README must follow."""
        edited = BLOCK.replace(was, now)

        assert edited != BLOCK, "the parametrized field is no longer in the caption"
        assert normalise(BLOCK) != normalise(edited)

    def test_the_trailing_sentence_is_compared(self) -> None:
        priced = BLOCK.replace("table: none.", "table: 3.9 (3,309.12).")

        assert normalise(BLOCK) != normalise(priced)

    def test_rewrapping_a_caption_does_not_change_it(self) -> None:
        """The caption renders as one long line; wrapping it by hand is not a regression."""
        wrapped = BLOCK.replace(CAPTION, textwrap.fill(CAPTION, width=60))

        assert wrapped.count("\n") > BLOCK.count("\n")
        assert normalise(wrapped) == normalise(BLOCK)

    def test_blank_lines_around_the_table_do_not_matter(self) -> None:
        assert normalise(BLOCK) == normalise(BLOCK.replace("\n\n", "\n\n\n"))

    def test_prose_and_table_keep_their_order(self) -> None:
        caption_first = "Recorded today.\n\n| A |\n|---|\n| 1 |"
        table_first = "| A |\n|---|\n| 1 |\n\nRecorded today."

        assert normalise(caption_first) != normalise(table_first)


def test_the_failure_message_carries_the_replacement() -> None:
    message = replacement_message("accuracy", "| A |\n|---|\n| 1 |")

    assert "out of date" in message
    assert "| A |" in message
