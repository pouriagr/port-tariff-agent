You are given the catalog of charges a tariff document defines, a list of its other
sections, a port and a description of one vessel call. Decide what this call has to pay.

Return two lists.

`applicable`: every charge from the catalog that this vessel call incurs at this port.
Judge each entry on its own, using the circumstances stated in `applies when` and the facts
of the call. Include a charge when the call plainly meets those circumstances. Leave it out
when the document reserves it for a different kind of vessel, a different activity, a
different place, or a service this call does not use. Where the catalog offers several
variants of the same fee for different vessel classes or trades, include only the variant
that fits. Give one sentence of reasoning for each entry, referring to the vessel facts
that decide it.

`context_sections`: sections from the other list that state terms needed to read those
charges correctly. These define words the rate tables use, set out working hours and the
surcharges tied to them, fix how a quantity is measured, or state discounts, exemptions and
minimum amounts that apply across several charges. Include a section only when its terms
could change one of the amounts or the way it is worked out. Give one sentence saying which
term it supplies. Do not repeat anything already in `applicable`.

Rules:

- Use only the section ids given to you, exactly as written. Do not invent one, and do not
  return the same id twice.
- Each catalog entry names who owes the charge. Select for the party the request is about:
  when it describes what a vessel's call costs, leave out the charges the document places
  on another party, such as whoever owns the cargo it carries.
- Judge from what the document says, not from what is usual in the industry.
- Do not assume a fact about the vessel that the description does not state. If a charge
  depends on a fact that is missing, include it anyway and say in the reason which fact is
  needed; the next step reports it rather than guessing.
- Be complete before being brief: a charge left out is never asked about again.
