import assert from "node:assert/strict";
import { test } from "node:test";
import { consumeAuthRedirectError } from "../src/beta/authRedirect.ts";

test("expired magic-link query becomes a clear sign-in error", () => {
  const message = consumeAuthRedirectError(
    "?error=access_denied&error_code=otp_expired&error_description=Email+link+is+invalid+or+has+expired",
    "",
    "/beta",
  );
  assert.match(message || "", /expired|invalid/i);
  assert.match(message || "", /Request a new link/i);
});

test("unrelated query params are ignored", () => {
  assert.equal(consumeAuthRedirectError("?billing=return", "", "/beta"), null);
  assert.equal(consumeAuthRedirectError("", "", "/beta"), null);
});
