You are reading one section of a tariff document and deciding what it is.

Question: does this section define a fee, due, charge, rate or levy that somebody has to
pay?

Answer `defines_charge: true` only when the section itself states or sets out an amount
payable, or the rule for computing one. A section that merely defines terms, explains
procedure, lists working hours or cross-references another section does not define a
charge.

When it does define one:

- `charge_name`: the name as the document calls it.
- `payer`: who owes it, as one of `vessel`, `cargo_owner`, `other`.
- `applies_when`: one sentence stating the circumstances in which it becomes payable.

When it does not, leave those fields empty.

`ports_mentioned`: the ports this section's rates or rules apply to, as the document names
them, including the ports named in table column headers. List each port separately. Where a
heading or a column groups several ports together, split it into one entry per port. Never
return a combined label.

Include only places that the document treats as ports of call. Do not include countries,
regions, authorities, berths, terminals, quays, dry docks or other facilities inside a port,
and do not include a column that stands for the ports not named elsewhere in the table.
Return an empty list when the section names no port.

The section is given below, preceded by the chain of headings it sits under.
