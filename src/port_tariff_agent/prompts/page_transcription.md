You are transcribing one page of a tariff book into Markdown.

Transcribe. Do not summarise, do not omit anything, do not add commentary of your own.

The page may contain two logical pages printed side by side. Transcribe the left one in
full, then the right one. Immediately before each, emit a marker holding the page number
printed in that logical page's footer:

    <!-- printed-page: 12 -->

If a footer has no readable number, emit `<!-- printed-page: unknown -->` instead. Never
guess a number and never calculate one.

Headings:

- Every heading that carries a number becomes a Markdown heading, with the number and the
  title exactly as printed. The heading level follows the depth of the number: one `#` for
  a single number, two for one dot, three for two dots, and so on.
- A heading with no number becomes bold text on its own line, not a heading.

Tables:

- Every table becomes a GitHub-flavoured Markdown table with exactly one header row.
- Where the source spans a header across several columns, or merges cells down a column,
  repeat the value in every cell it covers, so that each row stands on its own.
- Keep the column order as printed, and keep every column, including any column that acts
  as a general fallback for cases the named columns do not cover.
- Keep cells that say the charge does not apply exactly as printed; never blank them.

Numbers and text:

- Copy every number exactly as printed, including any thousands separators and decimal
  marks.
- A line of dots leading from a label to an amount becomes either one table row or a
  single `label: amount` line.
- Keep conditions, bullet lists, footnotes, provisos and any surcharge text with the
  section they belong to.

Return the transcription as Markdown in the `markdown` field.
