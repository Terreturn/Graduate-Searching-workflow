"""Decompose one turn, then check its user-facing language before storage."""
from clarifications import update_questions
from model_call import call_model as _call_model
from rules import PROPOSAL, STRING, array, choice, record, validate, validate_schema


LANGUAGE_REVIEW = record({
    "verdict": choice(["match", "revise"]), "summary": STRING, "questions": array(STRING),
})


def decompose(context, message, correction=None):
    payload = {"context": context, "utterance": message}
    if correction:
        payload["previous_validation_error"] = correction
    proposal = _call_model("decompose.md", payload, PROPOSAL)
    validate(proposal, message)
    update_questions(context.get("pending_questions", []), "", message, proposal)
    original = {"summary": proposal["summary"], "questions": proposal["questions"]}
    reply = original
    recent = [turn["message"] for turn in context.get("recent", [])]
    for attempt in range(2):
        if not reply["summary"].strip() or any(not question.strip() for question in reply["questions"]):
            raise ValueError("The summary and each clarification question must contain text.")
        review_input = {"utterance": message, "recent_utterances": recent, **reply}
        if attempt:
            review_input["original_display"] = original
        review = _call_model("check-language.md", review_input, LANGUAGE_REVIEW, effort="low")
        validate_schema(review, LANGUAGE_REVIEW)
        if review["verdict"] == "match":
            return {**proposal, **reply}
        if attempt or len(review["questions"]) != len(reply["questions"]):
            break
        reply = {"summary": review["summary"], "questions": review["questions"]}
    raise RuntimeError("The reply did not pass its language check. Retry using the same turn ID.")
