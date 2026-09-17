# Launch closure patch notes

## Branch
`fix/launch-closure-ai-contact-and-requirements`

Base commit (preserved main / PR #1–#6 work): `93cfd1f621dd6c4d39e2bf075e131dd2278aa210`

## Commit SHA(s)
- `aaff086d70b231b683f9c47b087279b8af2594a3` — Fix launch-closure AI contact leakage and requirements bullet merge.
- Docs commit on branch tip adds this file (`git log --oneline 93cfd1f..HEAD`).

## Files changed
- `src/jobagent/mobile/studio.py` — bare-phone stripping; scrub profile/preferences/answers/qualifications (and confirmed_answers / qualification dumps) in `_context` before model payloads. Document contact blocks still render via `_identity` from the raw profile.
- `src/jobagent/mobile/discovery_relevance.py` — `_segments` detects list markers before stripping them and does not join lowercase bullets onto a colon heading.
- `tests/test_mobile_studio.py` — regression coverage for bare phones, structured-field emails, metrics retention, and document contact preservation.
- `tests/test_discovery_relevance.py` — regression coverage for lowercase bullets, mixed-case/preferred/credentials, and genuine wrapped continuations.
- `LAUNCH_PATCH_NOTES.md` — apply/push instructions and residual risks.

## Test results

### HEAD-fail proof (unpatched `93cfd1f`, new tests only)
Commands:
```bash
cd /workspace/job-pursuit-launch
.venv/bin/python -m unittest tests.test_mobile_studio.LaunchClosureContactMinimizationTests -v
.venv/bin/python -m unittest tests.test_discovery_relevance.LaunchClosureRequirementsSegmentationTests -v
```
Results:
- Contact suite: **Ran 3, FAILED (failures=2)** — `test_without_contact_strips_bare_phones_and_keeps_metrics` FAIL; `test_context_filters_structured_fields_answers_and_resume` FAIL; `test_document_identity_keeps_original_contact_blocks` ok
- Requirements suite: **Ran 3, FAILED (failures=1)** — `test_lowercase_bullets_after_requirements_heading_stay_separate` FAIL; wrap + mixed-case tests ok

### After fix (commit `aaff086`)
Commands:
```bash
cd /workspace/job-pursuit-launch
.venv/bin/python -m unittest discover -s tests -p 'test_mobile_studio.py'
.venv/bin/python -m unittest discover -s tests -p 'test_discovery_relevance.py'
```
Results:
- `test_mobile_studio.py`: **Ran 42, OK** (0 failures)
- `test_discovery_relevance.py`: **Ran 27, OK** (0 failures)
- New suites alone: **Ran 6, OK**

## Residual risks
- Postal addresses in free text are not stripped; do not claim anonymization or E2EE.
- Some email-like strings in qualification notes may still be dropped entirely by the existing `_URL` gate rather than scrubbed-in-place (phones in those notes are now scrubbed and kept).
- Lowercase requirement lines **without** list markers after a non-colon heading can still merge; the launch bug was bullet + colon-heading merge.
- No push, CI, or deploy was performed from this environment.

## Mac apply / push commands

Patch file produced on this machine: `/workspace/job-pursuit-launch-fixes.patch`
(`git diff 93cfd1f621dd6c4d39e2bf075e131dd2278aa210..HEAD`)

On the Mac repo:
```bash
cd /Users/Sunil/2026/agents/job-search-agent
git fetch origin
git checkout 93cfd1f621dd6c4d39e2bf075e131dd2278aa210
git checkout -b fix/launch-closure-ai-contact-and-requirements
# copy job-pursuit-launch-fixes.patch onto the Mac, then:
git apply /path/to/job-pursuit-launch-fixes.patch
# or, if you prefer a 3-way apply:
# git apply --3way /path/to/job-pursuit-launch-fixes.patch
git add src/jobagent/mobile/studio.py \
        src/jobagent/mobile/discovery_relevance.py \
        tests/test_mobile_studio.py \
        tests/test_discovery_relevance.py \
        LAUNCH_PATCH_NOTES.md
git commit -m "Fix launch-closure AI contact leakage and requirements bullet merge."
# verify
python -m unittest discover -s tests -p 'test_mobile_studio.py'
python -m unittest discover -s tests -p 'test_discovery_relevance.py'
# push when ready (credentials required on the Mac)
git push -u origin fix/launch-closure-ai-contact-and-requirements
```

Alternative if the Mac already has commits on top of `93cfd1f`: cherry-pick is not available from this clone (no push). Apply the patch onto a branch based at `93cfd1f`, or recreate the two function edits from this diff.

## Not done from this environment
- No `git push` (no GitHub credentials; do not use `gh auth`)
- No CI run / deploy
- No job applications, charges, or launch-approval claims
