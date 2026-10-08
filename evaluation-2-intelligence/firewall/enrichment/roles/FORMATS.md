# Role formats and registries

Checked on 2026-10-08. Plain words. Rule: a pattern only raises a negative signal when it was verified from an official or authoritative document. Anything else is unverified and gives no signal at all.

## Format table

| Item | Pattern used | Penalty on mismatch | Source | Confidence |
| --- | --- | --- | --- | --- |
| ORCID iD | 16 characters in four groups of four, last character 0 to 9 or X, ISO 7064 MOD 11-2 checksum | yes, checksum failure | support.orcid.org article "Format of the ORCID iD" (https://support.orcid.org/hc/articles/360006897674) | Verified from the ORCID specification |
| arXiv new style | yymm.nnnn for 0704 to 1412, yymm.nnnnn from 1501, optional vN | yes | https://info.arxiv.org/help/arxiv_identifier.html | Verified from the official arXiv page |
| arXiv old style | archive/yymmnnn, optional subject class, 9108 to 0703 | yes | same page | Verified from the official arXiv page, the date window is inferred |
| WIPO PCT publication number | WO, four digit year, slash, six digits. Compacted form WOyyyynnnnnn | yes | PCT Administrative Instructions Section 404 (https://www.wipo.int/pct/en/texts/ai/s404.html) | Verified from the official text. Older seven digit forms such as WO02/12345 are treated as unknown, no penalty |
| US patent | Granted: US plus 7 or 8 digits, optional kind code B1 or B2. Publication: US plus year plus 7 digits plus A1 or A2. Design D, reissue RE, plant PP | yes, only for numbers starting with US that fit none of these | USPTO kind code page (https://www.uspto.gov/learning-and-resources/support-centers/electronic-business-center/kind-codes-included-uspto-patent) and MPEP 901.04 | Verified for the kind codes and the 7 digit and 11 digit shapes. The 8 digit granted length is inferred from number growth |
| EPO publication number | EP plus 6 or 7 digits plus kind code A or B with optional digit | none | EPO number format pages describe DOCDB and EPODOC forms, no fixed digit count was found | Unverified. A match is accepted, a mismatch gives no signal |
| India patent application number | IN plus 12 digits: year, office digit 1 to 4, type digit, six digit serial. In force since 2016-01-01 | none | Law firm summaries of the CGPDTM notice (ssrana.in and others). The CGPDTM notice itself was not fetched | Inferred from secondary reports. A match is accepted, a mismatch gives no signal. Granted patent numbers (older six or seven digit) are unknown |
| IEEE DOI | Starts with 10.1109/, rest is free text | yes, only the missing prefix | IEEE Xplore pages show the 10.1109 prefix. The DOI handbook allows any printable suffix | Prefix verified from IEEE Xplore records. Suffix has no format claim |
| MCA DIN | 8 digits | none | Eight digits is stated by many secondary guides. mca.gov.in pages found do not state the digit count in text we could read, and the page fetch returned 403 | Inferred. A match is accepted, a mismatch gives no signal |
| SEBI registration number | Prefix INA, INB, INH, INM, INP or INZ plus 9 digits | none | Examples in public disclosures by regulated firms (Citi, Axis, ICICI). SEBI publishes no format document that we found | Inferred from examples. A match is accepted, a mismatch gives no signal |
| ICAI membership number | Reported as 6 digits (ICAI phone line asks for a 6 digit number) | none | Search result snippet only. No ICAI format document found | Unverified. No pattern is used at all |
| ACCA membership number | No public format found | none | ACCA pages found do not state one | Unknown. No pattern is used |
| CFA Institute member id | No public id is exposed | none | The CFA Institute Member Directory (directory.cfainstitute.org) verifies status by name. No membership number is shown | None exists publicly. No pattern is used |
| Credential or certificate id | No universal format exists | weak plausibility only | Issuer specific | This is a plausibility test (too short, one repeated character, a plain run, no digit), not a format claim. Left as a low weight signal |

## Lookups

Built and active only when FIREWALL_ENRICH=1:

1. ORCID public API. Host pub.orcid.org, path /v3.0/{id}/person, no key, no CAPTCHA. ORCID documents 12 requests per second and a daily read quota for anonymous use. The ORCID Public API terms allow free non commercial use only, so the owner must confirm that this deployment qualifies, or move to an ORCID Member API credential. Found record with a matching name gives ORCID_VERIFIED. A 404 gives ORCID_NOT_FOUND. A different name gives ORCID_NAME_MISMATCH. Any other outcome is unknown and silent.
2. Crossref and OpenAlex author works are already handled by scholar.py and are not repeated here.
3. USPTO Open Data Portal patent search (api.uspto.gov). It needs a free API key, so the adapter turns on only when the owner sets FIREWALL_USPTO_ODP_API_KEY. Without the key it returns unknown. A request without a key was seen to answer 401 on 2026-10-08. The response shape was written from the ODP documentation and could not be proven live without a key. For that reason a patent that is not returned counts as unknown, never as not found. A returned patent with the candidate among the inventors gives PATENT_VERIFIED. A returned patent without the candidate gives PATENT_INVENTOR_MISMATCH.

Not used, with the reason:

- PatentsView search API. It needed a key and the service was reported shut down on 2026-03-20.
- Google Patents pages. There is no official API and no stated permission for automated queries, so no calls are made.
- EPO Open Patent Services and WIPO PATENTSCOPE. Both need a registered account.

Every adapter uses fixed https hosts only, no redirects, 4 second timeout, 256 KB response cap, a JSON content type check, a clear User-Agent, shape checks on the payload, a one hour in memory cache and silent fallback to unknown.

## What the owner must supply

- ICAI. No free automated lookup. The member search at icai.org is a web form meant for people. The owner needs a written permission or data feed from ICAI, or a paid verification provider contract (several Indian identity API vendors sell CA membership checks) with an API key. Until then the check stays unknown.
- SEBI. The intermediary and investment adviser lists on sebi.gov.in are web pages for people and the registration lookups are not offered as an open API. The owner needs either written permission and a data export from SEBI, or a licensed data vendor key. Until then unknown.
- MCA (directorship and DIN). The MCA portal needs a login and a CAPTCHA for master data. The owner needs an MCA V3 account with a licence for automated use, or a paid company data vendor key (for example an official MCA data reseller). Until then unknown.
- CFA. The Member Directory shows status by name only, with no public id and no stated licence for automated queries. The owner needs written permission from CFA Institute, or the candidate must share a verification link the candidate generates. Until then unknown.
- USPTO. A free ODP API key set as FIREWALL_USPTO_ODP_API_KEY to switch patent lookups on.
