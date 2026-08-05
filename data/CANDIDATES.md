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

Next: plans whose developer portals publish an explicitly open sandbox base URL; state Medicaid
managed-care plans; CMS-aligned reference implementations.
