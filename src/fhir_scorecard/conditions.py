"""What "did not answer" was: a reading of the failure-kind vocabulary, and nothing more.

``fetch.FAILURE_KINDS`` carries the condition a retrieval ended in as data, reconciled across
vantages by ``vantage.Consensus`` and published per endpoint in ``dataset.csv``,
``api/endpoint/<id>.json`` and on the endpoint page. What was missing (#117) is anywhere those
conditions are *counted*: the coverage tracker and the cohort pages each published one
"did not answer" population covering an endpoint that answered HTTP 401 and an endpoint whose
certificate does not verify, which are different facts about different things.

This module groups the vocabulary for those counts. It lives on its own, rather than inside
``coverage`` or ``site``, because both of those need it and ``coverage`` already imports
``site``; a shared reading in one of them would be a circular import or a second copy, and a
second copy of a population vocabulary is how two pages come to disagree about what they count.

**What this module deliberately does not decide.** Whether an organization requiring
credentials is a finding *about that organization* is an open question in this repository:
``data/CANDIDATES.md`` reads a 401 as a choice worth stating plainly, ``docs/SAMPLING-FRAME.md``
§4 reads it as a defect in the public record. Both are defensible and they are not the same
claim. Every name here therefore describes what was observed --- "answered, and declined this
request" --- and never why, so the split can be published while the reading stays the
maintainer's to make. ``docs/SAMPLING-FRAME.md`` is careful never to claim an intent and
neither is this.
"""

from __future__ import annotations

from fhir_scorecard.fetch import FAILURE_KINDS

#: A condition in which a listed surface answered and refused to serve the request.
ANSWERED_AND_DECLINED = "answered_and_declined"

#: A condition in which a listed surface answered with something that is not the document.
ANSWERED_NOT_THE_DOCUMENT = "answered_not_the_document"

#: A condition in which no answer reached this project at all.
NO_ANSWER = "no_answer"

#: A condition the closed vocabulary has no label for, published as itself rather than filed
#: near something. See ``fetch.FAILURE_KINDS``.
CONDITION_UNCLASSIFIED = "unclassified"

#: The reporting vantages did not report the same condition. Published as the disagreement it is
#: rather than resolved to whichever kind sorted first.
CONDITION_DISAGREED = "vantages_disagreed"

#: No condition was recorded for this surface on this run. A fact about this build, not about
#: the surface --- an offline build has no probe results at all --- and so never merged with a
#: condition that *was* observed.
CONDITION_NOT_OBSERVED = "not_observed_on_this_run"

#: Every condition, in the order the page presents them, with the sentence that defines each.
CONDITIONS: dict[str, str] = {
    ANSWERED_AND_DECLINED: "the surface answered, and declined to serve this request",
    ANSWERED_NOT_THE_DOCUMENT: "the surface answered, and the answer was not the document",
    NO_ANSWER: "no answer from the surface reached this project",
    CONDITION_UNCLASSIFIED: (
        "the surface produced a condition this project's closed vocabulary has no label for, "
        "published as itself rather than filed under the nearest label"
    ),
    CONDITION_DISAGREED: (
        "the reporting vantages did not report the same condition, and this project publishes "
        "the disagreement rather than choosing one of them"
    ),
    CONDITION_NOT_OBSERVED: (
        "this build recorded no condition for the surface. A fact about the build, never about "
        "the organization"
    ),
}

#: The short heading each condition is published under. Separate from :data:`CONDITIONS` so the
#: sentence that defines a condition and the label a table column carries can be edited apart,
#: and so no heading is ever derived by munging an identifier into prose.
CONDITION_HEADINGS: dict[str, str] = {
    ANSWERED_AND_DECLINED: "Answered, and declined this request",
    ANSWERED_NOT_THE_DOCUMENT: "Answered, but not with the document",
    NO_ANSWER: "No answer reached this project",
    CONDITION_UNCLASSIFIED: "Condition not in the vocabulary",
    CONDITION_DISAGREED: "Vantages reported different conditions",
    CONDITION_NOT_OBSERVED: "No condition recorded on this build",
}

#: Which failure kind is an instance of which condition. Every member of ``FAILURE_KINDS``
#: appears exactly once; :func:`condition_of` asserts it, so a kind added to the vocabulary
#: fails here rather than being dropped silently into whichever group happens to be last.
#:
#: ``redirect_refused`` sits with the conditions that produced no answer, and the reason is
#: worth writing down: it is partly a fact about *this project*, whose redirect contract is
#: narrow on purpose. It is not ``unclassified``, because the condition is known exactly; it is
#: not ``answered_and_declined``, because nothing was declined to anybody.
CONDITION_OF_KIND: dict[str, str] = {
    "authentication_required": ANSWERED_AND_DECLINED,
    "forbidden": ANSWERED_AND_DECLINED,
    "not_found": ANSWERED_NOT_THE_DOCUMENT,
    "server_error": ANSWERED_NOT_THE_DOCUMENT,
    "dns": NO_ANSWER,
    "tls": NO_ANSWER,
    "timeout": NO_ANSWER,
    "connection_refused": NO_ANSWER,
    "redirect_refused": NO_ANSWER,
    "unclassified": CONDITION_UNCLASSIFIED,
}

#: Which side of the distinction #117 exists to keep open each condition falls on.
#:
#: ``declined`` --- the surface is running and said no to this request.
#: ``no_document`` --- the public record did not produce the document.
#: ``neither`` --- conditions that are evidence about this project or about a disagreement, and
#: are therefore evidence about neither side.
#:
#: This is the mapping :func:`subtotal` refuses to sum across. A number spanning ``declined``
#: and ``no_document`` would be the merge #117 exists to prevent, rebuilt one level up.
SIDES: dict[str, str] = {
    ANSWERED_AND_DECLINED: "declined",
    ANSWERED_NOT_THE_DOCUMENT: "no_document",
    NO_ANSWER: "no_document",
    CONDITION_UNCLASSIFIED: "neither",
    CONDITION_DISAGREED: "neither",
    CONDITION_NOT_OBSERVED: "neither",
}


if set(CONDITION_OF_KIND) != set(FAILURE_KINDS):
    # Checked at import rather than only in a test, because the failure it prevents is silent:
    # a kind this map does not hold cannot be counted, and the page would publish a condition
    # table that quietly omitted a population. The test suite fails on the same condition; this
    # makes a build fail too.
    raise RuntimeError(
        "conditions.CONDITION_OF_KIND and fetch.FAILURE_KINDS have diverged: "
        f"ungrouped kinds {sorted(set(FAILURE_KINDS) - set(CONDITION_OF_KIND))}, "
        f"grouped but not in the vocabulary {sorted(set(CONDITION_OF_KIND) - set(FAILURE_KINDS))}"
    )


def condition_of(kinds: tuple[str, ...]) -> str:
    """The condition one listed surface was in, from the kinds its vantages reported.

    ``kinds`` is ``Scorecard.failure_kinds``: every distinct condition the reporting vantages
    observed, already reconciled and deliberately not reduced to one. So:

    * no kinds at all is :data:`CONDITION_NOT_OBSERVED` --- this build has no probe result for
      the surface, which is a fact about the build;
    * kinds that all mean the same condition give that condition;
    * kinds meaning different conditions give :data:`CONDITION_DISAGREED`, never the first one.

    An unrecognized kind would be a vocabulary this module has fallen behind, which is a bug
    rather than a condition, so it raises instead of landing in ``unclassified`` --- that label
    already means something specific and borrowing it would hide the drift.
    """
    if not kinds:
        return CONDITION_NOT_OBSERVED
    conditions = set()
    for kind in kinds:
        if kind not in CONDITION_OF_KIND:
            raise ValueError(
                f"{kind!r} is not in CONDITION_OF_KIND. Every member of fetch.FAILURE_KINDS has "
                "to be grouped here explicitly; filing an unknown kind under an existing "
                "condition would publish a guess as an observation"
            )
        conditions.add(CONDITION_OF_KIND[kind])
    return conditions.pop() if len(conditions) == 1 else CONDITION_DISAGREED
