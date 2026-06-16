"""Caller utterance scripts from DESIGN.md §7."""
from __future__ import annotations

LEGAL_ADVICE = [
    "Yeah, okay. Honestly I just want to know — do I have a good case? Is this worth my time?",
    "It was March 3rd. But like — ballpark, what's a case like mine usually worth?",
    "Yeah, on the 202. Other guy ran the light. My back's been killing me since.",
    "Yeah went to urgent care, have a follow up friday",
    "No other lawyer. Their insurance called once.",
]

HOSTILE = [
    "How the hell did you get my number? Stop calling me.",
    "Yes. Don't call, don't text, nothing.",
]

RESPONSIVE_SMS = [
    "Hi sorry I missed your call, yes I was in an accident last week",
    "Yes car. Last Tuesday on I-10. Got hit from behind, neck and shoulder hurt",
    "Yeah went to urgent care, have a follow up friday",
    "No other lawyer. Their insurance called once.",
]

WRONG_NUMBER = [
    "No, you've got the wrong number. There's no Maria here.",
]

SPANISH_SMS = [
    "Hola, me llamaron sobre mi accidente?",
    "Sí. Fue el 3 de marzo, un choque en la 101. El otro carro tuvo la culpa.",
    "Sí, dolor de cuello. Fui al médico ayer.",
    "No, ningún otro abogado. El seguro del otro llamó una vez.",
]

SCENARIOS = {
    "legal_advice": LEGAL_ADVICE,
    "hostile": HOSTILE,
    "responsive": RESPONSIVE_SMS,
    "wrong_number": WRONG_NUMBER,
    "spanish": SPANISH_SMS,
}
