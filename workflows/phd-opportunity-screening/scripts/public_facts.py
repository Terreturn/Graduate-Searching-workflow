"""Publish material values from independently collected, typed observations."""
from datetime import date, datetime
from decimal import Decimal
import re

from rules import STRING, TUITION_RATE, choice, record, valid_amount, validate_schema

STIPEND = record({"amount": STRING, "currency": STRING,
                  "period": choice(["once", "month", "term", "year", "program_total", "week"])})
DEADLINE = record({"iso": STRING})
OBSERVATIONS = {"tuition": TUITION_RATE, "stipend": STIPEND, "deadline": DEADLINE}


def rate_key(rate):
    if not valid_amount(rate):
        return None
    return (Decimal(rate["amount"]), *(rate.get(k) for k in ("currency", "period", "fee_status", "charges")))


def deadline_key(value):
    # Preserve the publisher's stated local date/time and UTC offset.
    iso = value.get("iso", "")
    try:
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", iso):
            return date.fromisoformat(iso).isoformat()
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(:[0-9]{2})?(Z|[+-][0-9]{2}:[0-9]{2})?", iso):
            return datetime.fromisoformat(iso.replace("Z", "+00:00")).isoformat()
    except ValueError:
        pass
    return None


def render_fact(cell, pair, proofs, url_key, tuition_rates):
    field, template = cell["field"], cell["value"]
    observation = cell.get("observation")
    token = "{date}" if field == "deadline" else "{amount}"
    def key(value):
        return deadline_key(value) if field == "deadline" else rate_key(value)

    if observation is not None and field in OBSERVATIONS:
        try:
            validate_schema(observation, OBSERVATIONS[field])
        except ValueError:
            return None
    if observation is not None:
        if field not in OBSERVATIONS or template.count(token) != 1 or key(observation) is None:
            return None
        if field == "tuition" and tuition_rates and key(observation) not in tuition_rates:
            return None
    elif (field == "tuition" and tuition_rates or field in OBSERVATIONS and re.search(r"\d", template)
          or "{amount}" in template or "{date}" in template):
        return None

    # The auditor selects one sourced wording; quantities and dates must agree on both sides.
    cited = {url_key(url) for url in cell["urls"]}
    wording_urls = set()
    for check, proof in zip(pair, proofs):
        corroborated = False
        for fact in check["facts"]:
            if fact["field"] != field:
                continue
            pages = {url_key(url) for url in fact["urls"]} & proof
            if not pages:
                continue
            other = fact.get("observation")
            if observation is not None:
                if other is None or key(other) != key(observation):
                    continue
            elif other is not None:
                continue
            # Numeric qualifiers in narrative facts must also appear in the other reading.
            if re.findall(r"\d+(?:[.,]\d+)*", fact["value"]) != re.findall(r"\d+(?:[.,]\d+)*", template):
                continue
            if observation is None and (field in OBSERVATIONS or re.search(r"\d", template)):
                if fact["value"] != template:
                    continue
            corroborated = True
            if fact["value"] == template:
                wording_urls.update(pages)
        if not corroborated:
            return None
    if not cited or not cited <= wording_urls:
        return None
    if observation is None:
        return template
    if field == "deadline":
        return template.replace(token, observation["iso"])
    amount = format(Decimal(observation["amount"]), ",f")
    if "." in amount:
        amount = amount.rstrip("0").rstrip(".")
    return template.replace(token, observation["currency"] + " " + amount)
