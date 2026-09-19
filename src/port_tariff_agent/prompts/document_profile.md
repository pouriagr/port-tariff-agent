You are reading the opening pages of a tariff document and recording what it is.

Return:

- `issuer`: the authority or company that publishes it.
- `title`: the document's own title, including any edition wording.
- `currency`: the currency its amounts are stated in, as a three-letter ISO code.
- `valid_from` and `valid_to`: the first and last day of the period it applies to, each as
  `YYYY-MM-DD`.

Use only what the pages actually state. Where the document gives a period in words, convert
it to dates. Where a value is not stated at all, return it as null rather than inferring
one. Do not guess an edition, a currency or a date from context.

The opening pages follow.
