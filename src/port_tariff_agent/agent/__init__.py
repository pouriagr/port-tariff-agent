"""Query phase: the TariffAgent ReAct loop and its tools.

Tools (see docs/spec/query.md):
- get_charges(port, vessel_description, arrival_date): selects the document,
  runs the ChargeSelector and returns the text of the applicable sections.
- calculate(expression): evaluates a formula written by the agent. The LLM
  never does arithmetic itself.
- submit_answer(answer): control tool that ends the loop with a validated
  TariffAnswer.
"""

from .answer import ChargeLine, NotApplicableLine, TariffAnswer
from .calculator import evaluate
from .knowledge import Document, get_charges
from .loop import TariffAgent
from .selector import ChargeSelector, SelectorResponse
from .tools import Toolbox

__all__ = [
    "ChargeLine",
    "ChargeSelector",
    "Document",
    "NotApplicableLine",
    "SelectorResponse",
    "TariffAgent",
    "TariffAnswer",
    "Toolbox",
    "evaluate",
    "get_charges",
]
