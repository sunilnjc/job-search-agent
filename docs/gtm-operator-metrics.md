# GTM operator metrics, trust page, and weekly dogfood export

These tools are **operator/founder read paths**. They are not a production
publish, not live Stripe charges, and not real employer applications.

## 1. AI cost and unit burn per Studio packet

A completed Studio packet is **one succeeded assessment** (`rank_job`) plus
**one succeeded prepare** (`prepare_documents`, tailored resume + cover letter).

Metering is next to the LLM call: `input_tokens`, `output_tokens`, model name,
and `request_type` (`assessment` / `packet_prepare` / `chat`). Estimated USD uses
conservative public list prices (override with `MOBILE_AI_PRICE_TABLE` JSON; never
put API keys there). Reserved units come from `mobile_reserve_ai_usage`. Costs
are estimates, not invoices.

Persisted on `model_runs.output_summary` (no resume text, emails, or prompts):

- `usage` — tokens, estimated USD, reserved units, reservation id, request type
- `packet_burn` — on a succeeded prepare: assessment run id, totals, `incomplete`

### Local report

```bash
.venv/bin/python scripts/packet_burn_report.py --from-json path/to/model_runs.json
.venv/bin/python scripts/packet_burn_report.py --from-json path/to/model_runs.json --format csv
.venv/bin/python scripts/packet_burn_report.py --sql
```

`--from-json` accepts a list of `model_runs` rows or `{"model_runs": [...]}`.
Export only the columns you need (`id`, `user_id`, `job_id`, `operation`,
`status`, `completed_at`, `output_summary`). Do not include document bytes.

### Service-role SQL

Requires migration `0015_gtm_operator_metrics.sql`. In the SQL editor as
`service_role`:

```sql
select public.mobile_operator_packet_burn();
```

Authenticated browser roles cannot execute this function.

## 2. Structured fit explanations

Ranking still returns `rationale` for old clients. New additive fields:

```json
"fit_explanation": {
  "why": ["Fit estimate, not an ATS score or prediction of an interview."],
  "evidence": ["Confirmed information: … [career_text.0]"],
  "uncertainty": ["Work eligibility is unknown, not confirmed ineligible. …"]
}
```

The model still selects verbatim `source_facts`. The server structures why /
evidence / uncertainty. Job scores store optional `fit_explanation` JSONB.
Bootstrap and job payloads include the structured object, or a rationale-derived
fallback when older rows have only `rationale`. Studio UI is unchanged in layout;
it can render the three lists with existing prose styles.

## 3. Trust and safety page (unpublished by default)

Copy lives in the beta SPA. The route is **off unless both flags are set**:

| Flag | Default | Effect |
| --- | --- | --- |
| `MOBILE_TRUST_PAGE_ENABLED` | unset/false | Server serves `/beta/trust` (and redirects `/trust`) only when true |
| `VITE_TRUST_PAGE_ENABLED` | unset/false | Client renders the page; otherwise the path is treated as unpublished |

This PR does **not** enable either flag in production env examples beyond a
commented reminder. Do not publish the page by setting these on the Mac tunnel
host until the copy is reviewed.

When enabled, open `https://www.thejobpursuit.com/beta/trust`. The page covers
data isolation, no automatic employer submit, export/delete, and honest AI limits.
It does not require sign-in.

## 4. Weekly dogfood metrics

Counts for the **prior ISO week** (UTC) unless `--iso-week 2026-W37` is passed:

| Field | Meaning |
| --- | --- |
| `roles_reviewed` | Distinct jobs with a `job_scores` row or succeeded `rank_job` |
| `roles_prepared` | Distinct jobs with a succeeded `prepare_documents` run |
| `roles_applied` | Distinct jobs with application `status=submitted` in the week (`applied_at` or `updated_at`). User-recorded, not an employer receipt |
| `replies` | Always `null` / CSV `N/A` — inbound replies are not stored |

```bash
.venv/bin/python scripts/weekly_dogfood_metrics.py --from-json path/to/export.json
.venv/bin/python scripts/weekly_dogfood_metrics.py --from-json export.json --iso-week 2026-W37 --format csv
.venv/bin/python scripts/weekly_dogfood_metrics.py --sql --iso-week 2026-W37
```

JSON export shape:

```json
{
  "job_scores": [{"job_id": "…", "created_at": "2026-09-08T12:00:00Z"}],
  "model_runs": [{"job_id": "…", "operation": "prepare_documents", "status": "succeeded", "completed_at": "2026-09-08T13:00:00Z"}],
  "applications": [{"job_id": "…", "status": "submitted", "applied_at": null, "updated_at": "2026-09-09T09:00:00Z"}]
}
```

Service-role SQL (Monday of that ISO week):

```sql
select public.mobile_operator_dogfood_week('2026-09-07'::date);
```

No new analytics SDK is introduced. Do not put service-role keys in the repo.

## Apply order and blockers

- Apply `0015_gtm_operator_metrics.sql` before deploying this API revision. Job list/detail now select `job_scores.fit_explanation`; unmigrated databases will fail that nested select.
- Trust flags stay **off**. This PR does not publish `/beta/trust`.
- Packet burn USD figures are public list-price **estimates**, not Stripe invoices. Do not change billing config for this work.
- Disposable PostgreSQL SQL tests (`OperatorMetricsPostgresTests`) run in the offline regression gate on Linux with native `postgres`/`initdb`. They skip when those binaries are missing; that skip is a **gate failure** in CI, not a pass.
