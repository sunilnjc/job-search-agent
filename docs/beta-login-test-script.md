# Public beta login manual test script

Use this checklist after the route split is configured. The public beta is
`https://www.thejobpursuit.com/beta`; the founder dashboard remains at
`https://www.thejobpursuit.com/admin` behind Cloudflare Access.

Record the browser/device used and the result of each check. Do not use real
credentials or private documents belonging to another tester.

## Before testing

- [ ] Confirm `www.thejobpursuit.com/beta` loads the product sign-in
  screen.
- [ ] Confirm `www.thejobpursuit.com/admin` still opens the founder dashboard and
  requires the founder's Cloudflare Access policy.
- [ ] Use a normal browser window for returning-user checks and a private
  window (or separate browser profile) for new-user checks.

## New user

- [ ] Open `https://www.thejobpursuit.com/beta` in a private window.
- [ ] Confirm no Cloudflare Access screen appears.
- [ ] Enter a new test email address and request a passwordless link.
- [ ] Confirm the UI acknowledges the email without exposing account details.
- [ ] Open the link on the **same device**. It must return to
  `www.thejobpursuit.com/beta` and show onboarding.
- [ ] Complete onboarding and verify the user reaches only their own empty
  workspace.

## Returning user

- [ ] Sign out from the beta workspace.
- [ ] Request a new passwordless link using the existing test account.
- [ ] Open it on the same device and confirm it reaches the existing private
  workspace, not onboarding.
- [ ] Confirm the active session survives a normal page refresh and ends after
  sign-out.

## Magic-link device and error cases

- [ ] Request a link on Device A, then open it on Device B where the user is
  not signed in. Confirm Device B completes sign-in safely and returns to
  `www.thejobpursuit.com/beta`.
- [ ] Reuse the same link after it has been consumed. Confirm the app shows a
  clear, safe error or asks the user to request a new link; it must not create
  a second session unexpectedly.
- [ ] Open an expired link. Confirm the failure is understandable and offers a
  fresh sign-in request.
- [ ] Open a malformed or invalid link. Confirm no workspace data is revealed
  and the user can return to sign-in.

## Resend and provider limits

- [ ] Immediately request another link after the first request. Confirm the
  resend control shows a 60-second cooldown and cannot be clicked repeatedly.
- [ ] After the cooldown, request one new link and confirm it works.
- [ ] If Supabase reports an email rate limit, confirm the app explains that
  sending is temporarily unavailable, tells the user to wait, and does not
  claim that another resend was sent.
- [ ] Verify the product remains usable for an already signed-in user while
  new email sends are rate-limited.

## Private resume and document isolation

Create two separate test accounts, A and B.

- [ ] In account A, upload a harmless test resume and generate or save a test
  application artifact.
- [ ] In account A, confirm the file can be listed and downloaded from its
  Documents area.
- [ ] Sign out, then sign in as account B.
- [ ] Confirm account B cannot see, download, guess a URL for, edit, or delete
  account A's resume or artifacts.
- [ ] Confirm account B can upload and use its own test document normally.
- [ ] Return to account A and confirm its data is unchanged.

## Route split regression check

- [ ] Visit `https://www.thejobpursuit.com/beta` from a signed-out browser: only
  the product's passwordless sign-in should appear.
- [ ] Confirm no Cloudflare Access branding, Cloudflare account login, or
  founder-only allow-list is presented on the public app hostname.
- [ ] Visit `https://www.thejobpursuit.com/admin`: Cloudflare Access should still
  protect the founder dashboard.
- [ ] Confirm a magic link requested from `/beta` returns to that same beta
  route, never to the founder dashboard or a localhost address.

## Pass criteria

The route split is ready for beta use only when all login, resend, session,
and document-isolation checks pass, and `/beta` has no Cloudflare Access gate.
