# Candidate log

Every probed base URL, verified or not, so curation work is visible and nobody re-probes a
known-dead guess. One `/metadata` request per candidate per probe date.

## 2026-08-04

| Candidate | Base URL | Outcome |
|---|---|---|
| CMS Blue Button 2.0 | api.bluebutton.cms.gov/v2/fhir | **Verified** → registry |
| CMS Blue Button 2.0 sandbox | sandbox.bluebutton.cms.gov/v2/fhir | Verified but redundant with production (same software); not listed |
| Humana | fhir.humana.com/api | **Verified** → registry |
| Humana sandbox | fhir.humana.com/sandbox/api | Rejected: HTTP 403 |
| Aetna public demo | vteapif1.aetna.com/fhirdemo/v1/patientaccess | **Verified** → registry (demo instance, labeled as such) |
| Cigna Patient Access | fhir.cigna.com/PatientAccess/v1 | **Verified** → registry |
| Cigna alt path | fhir.cigna.com/r4 | Rejected: HTTP 403 |
| Optum/UHC public (guess) | public.fhir.flex.optum.com/R4 | Rejected: URLError (name did not resolve) |
| Molina (guess) | fhir.molinahealthcare.com/fhir/r4 | Rejected: URLError |
| Centene (guess) | api.centene.com/fhir/r4 | Rejected: HTTP 404 |
| ONC Inferno reference | inferno.healthit.gov/reference-server/r4 | **Verified** → registry (reference) |
| Firely public | server.fire.ly | **Verified** → registry (reference) |

## 2026-08-05

Second wave. Candidates sourced from public payer developer-portal documentation rather than
guessed from company names, which improved the hit rate but still failed most of the time. That
ratio is the point of keeping this log: published base URLs go stale, sit behind gateways that
refuse unauthenticated discovery, or were never public.

| Candidate | Base URL | Outcome |
|---|---|---|
| BCBS Minnesota | preview-api.bluecrossmn.com/fhir | **Verified** → registry (preview environment, labeled) |
| HealthPartners | api-developerportal.healthpartners.com/interop/external/fhir | **Verified** → registry |
| Capital Blue Cross | patientaccess-api.capbluecross.com/r4 | Rejected: DNS did not resolve |
| Capital Blue Cross demo | patientaccess-api-demo.capbluecross.com/r4 | Rejected: DNS did not resolve |
| MVP Health Care | patientaccess.mvphealthcare.com/fhir/r4 | Rejected: DNS did not resolve |
| Anthem/Elevance | fhir.anthem.com/api/v1 | Rejected: DNS did not resolve |
| Kaiser Permanente | healthy.kaiserpermanente.org/fhir/r4 | Rejected: HTTP 410 Gone |
| UnitedHealthcare | public-fhir.uhc.com/R4 | Rejected: DNS did not resolve |
| Centene | production.api.centene.com/fhir/patientaccess/r4 | Rejected: DNS did not resolve |
| Health Alliance Plan | api.hap.org/fhir/r4 | Rejected: HTTP 404 |

Standing conclusion after two waves: 6 of 22 probed candidates expose an unauthenticated
CapabilityStatement. Many plans gate `/metadata` behind registration, which is permitted but
makes their conformance publicly unverifiable. That is itself a finding worth stating plainly
rather than a gap in this dataset.

## 2026-08-05 (third wave)

Widened past payers to the surfaces that anchor comparison: EHR vendor sandboxes and a federal
provider API. These are recorded under distinct `kind` values because a payer Patient Access API
and an EHR sandbox answer to different implementation guides; the report groups by kind and never
ranks across them.

| Candidate | Base URL | Outcome |
|---|---|---|
| VA Lighthouse (production) | api.va.gov/services/fhir/v0/r4 | **Verified** → registry (`provider`) |
| VA Lighthouse (sandbox) | sandbox-api.va.gov/services/fhir/v0/r4 | Verified but redundant with production (same software); not listed |
| Epic on FHIR sandbox | fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4 | **Verified** → registry (`ehr`) |
| Oracle Health (Cerner) open | fhir-open.cerner.com/r4/ec2458f2-… | **Verified** → registry (`ehr`) |
| Medplum public API | api.medplum.com/fhir/R4 | **Verified** → registry (`ehr`) |
| Acentra Health sandbox | sandbox.mhbapp.com/fhir/r4 | Rejected: HTTP 403 |
| Acentra alt host | api.mhbapp.com/fhir/r4 | Rejected: DNS did not resolve |
| Elevance TotalView | totalview.healthos.elevancehealth.com/fhir | Rejected: answered HTML, not a CapabilityStatement |
| Elevance TotalView /r4 | totalview.healthos.elevancehealth.com/fhir/r4 | Rejected: answered HTML, not a CapabilityStatement |
| Logica Health sandbox | api.logicahealth.org/fhirserver/open | Rejected: DNS did not resolve |

## 2026-08-05 (fourth wave) and a correction to the headline statistic

Probed ten Medicaid managed-care and regional plans using the URL pattern many payers follow
(`patientaccess.<company>.com/fhir/r4`), plus re-probes of two earlier failures. **All twelve
failed**, ten of them because the hostname does not resolve.

| Candidate | Outcome |
|---|---|
| Healthfirst NY, Molina, EmblemHealth, HCSC/BCBSIL, Wellcare, CareFirst, Premera, BCBS MA, Highmark, Point32Health | Rejected: DNS did not resolve (pattern guesses) |
| Kaiser Permanente (re-probe) | Rejected: HTTP 410 Gone, unchanged since 2026-08-05 wave 2 |
| Centene (re-probe) | Rejected: DNS did not resolve, unchanged |

**Correction.** Earlier waves reported "6 of 22 payer candidates verified." That number
conflated two different populations: endpoints whose base URL was **documented** on a public
developer portal, and endpoints whose URL I **guessed** from a naming pattern. A guessed hostname
that does not resolve says nothing about the payer; it says the guess was wrong. Reporting them
together inflates a claim about industry practice using my own failed guesses as evidence.

Corrected counts, payers only:

| Population | Probed | Verified |
|---|---|---|
| Base URL documented on a public developer portal | 10 | **6** |
| Base URL guessed from a naming pattern | 18 | **0** |

The defensible claim is the first row, and even that is small-N. The second row is a fact about
guessing, not about payers. The zero is still informative in one narrow sense: payer FHIR base
URLs are not predictable from company names, so any registry of them has to be curated from
documentation rather than generated.

Next: source base URLs from CMS's own payer API listings and individual plan developer portals
rather than patterns; re-probe rejected payers quarterly, since a 404 today may be live later.
