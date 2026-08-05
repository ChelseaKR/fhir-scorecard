# Can you check a payer's FHIR API without asking permission first?

*Observations from building a public FHIR scorecard, August 2026.*

CMS interoperability rules require regulated payers to expose FHIR APIs so that people can get
their own claims and coverage data into an app of their choosing. A reasonable question, given
that: **can an outside party confirm those APIs exist and are conformant, without entering a
business relationship with each payer first?**

For most of the payers I tried, the answer is no. That is not necessarily non-compliance, and
this is not an audit. But it does mean the public cannot verify the thing the rule is about,
which seems worth writing down.

## What I did

I built [fhir-scorecard](https://github.com/ChelseaKR/fhir-scorecard), which fetches two public
documents from a FHIR endpoint and grades what it finds:

- `[base]/metadata`, the CapabilityStatement every FHIR server is required to expose
- `[base]/.well-known/smart-configuration`, the SMART on FHIR discovery document

Nothing authenticated. No patient data. One request per document per run. The grading is
deterministic, every finding cites the spec clause it rests on, and an unreachable endpoint
scores F with a stated reason rather than dropping out of the dataset.

Then I went looking for endpoints to point it at.

## The finding

Payer candidates fall into two populations that must be counted separately:

| Payer candidates | Probed | Verified |
|---|---|---|
| Base URL documented on a public developer portal | 10 | **6** |
| Base URL guessed from a naming pattern | 18 | **0** |

Only the first row supports any claim about payers, and n=10 is small. I am reporting the second
row because omitting it would be dishonest, not because it is evidence: a guessed hostname that
does not resolve tells you the guess was wrong, nothing more. Early drafts of this work conflated
the two into "6 of 22," which inflated a claim about industry practice using my own bad guesses.
That was corrected in the repository's candidate log.

The one inference the zero does support is narrow and practical: **payer FHIR base URLs are not
predictable from company names.** Any registry of them has to be curated from documentation,
one plan at a time.

The contrast that makes this interesting is with the other side of the industry. Every EHR
vendor sandbox I tried answered on the first attempt:

| Endpoint | Result |
|---|---|
| Epic on FHIR sandbox | Open CapabilityStatement, grade A |
| Oracle Health (Cerner) open sandbox | Open CapabilityStatement, grade C |
| Medplum public API | Open CapabilityStatement, grade A |
| VA Lighthouse (production) | Open CapabilityStatement, grade A |

EHR vendors treat an open, unauthenticated discovery surface as table stakes, because developers
evaluate their platforms. Payers largely do not, because in most cases nobody is shopping.

Among payers that *are* publicly checkable, the results are good: Humana, Cigna, BCBS Minnesota,
and HealthPartners all grade A. CMS Blue Button 2.0 grades B. This is not a story about payers
building bad APIs. It is a story about who is in a position to know.

## What "publicly checkable" is worth

The CapabilityStatement is the machine-readable answer to "what does this server actually
support." When it is reachable without credentials, several things become possible for anyone,
not just a registered partner:

- confirming an API exists at all, and which FHIR release it serves
- seeing which resource types and interactions are supported before writing code
- checking whether US Core, CARIN, or Da Vinci profiles are declared
- watching for changes over time, which is why the scorecard fingerprints declared capability
  each run and reports drift

When it is behind registration, all of that is available only to parties who have already
committed to the integration, and to regulators. That may be entirely acceptable as policy. It
is still a difference worth naming, because "the API is required to exist" and "the API can be
independently observed to exist" are not the same property, and only the second one degrades
gracefully when nobody is looking.

## Things I got wrong along the way

Worth stating, since methodology sections usually only report the version that worked:

**Conflated populations.** As above. Guessed URLs and documented URLs were counted together
until the fourth probing wave, when twelve consecutive failures made the problem obvious.

**Graded narrow APIs as deficient.** The first version rewarded breadth, so CMS Blue Button 2.0
lost points for declaring exactly three resource types. That is a deliberate scoping decision
with every resource fully documented. Narrow-but-complete now earns full credit.

**Graded a public-by-design API as insecure.** Provider Directory APIs are required to be
reachable *without* authentication. The grader marked Cigna's directory down for having no OAuth
surface, which penalized correct behavior. Those findings are now not-applicable for that kind.

**Graded R5 servers as failing R4.** The version check assumed R4 universally. Endpoints now
declare which release they intend to serve and are checked against that.

Three of those four are the same error: treating one API shape's expectations as universal. A
grader that cannot say "not applicable" will eventually punish something for being correctly
different.

## Caveats

Small n. Latency is measured from a single vantage point per run and the bands are deliberately
coarse for that reason. Grades are comparable only within a kind, so the report never ranks a
payer API against an EHR sandbox. Nothing here is an audit, a compliance determination, or a
statement about anyone's care quality; it is a snapshot of public surfaces on a given day.

Every probe, including all 45 rejections and their failure modes, is in
[`data/CANDIDATES.md`](../data/CANDIDATES.md). The live scorecard is at
[chelseakr.github.io/fhir-scorecard](https://chelseakr.github.io/fhir-scorecard/) and regrades
daily.

If you work at a payer whose endpoint I could not reach and it is in fact publicly available,
please open an issue with the base URL. I would rather be corrected than counted right.
