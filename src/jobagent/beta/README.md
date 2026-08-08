# Beta API foundation

This package contains the **route-independent** authentication and request
validation building blocks for the multi-user beta. It does not access the
founder's SQLite database, profile files, documents, browser profile, or any
existing single-user API routes.

## Required dependencies

The existing web extra already supplies FastAPI and the project supplies
`httpx` and Pydantic. Add the following dependency before importing
`jobagent.beta.auth` in a deployed beta API:

```text
PyJWT[crypto]>=2.8,<3
```

`cryptography` is required by PyJWT to verify Supabase asymmetric signing keys.

## Required configuration

Set these server-side environment variables. They are public project metadata,
not service-role credentials. Never put a Supabase service-role key in a
browser or in a user-request authentication path.

```dotenv
BETA_SUPABASE_URL=https://<project-ref>.supabase.co
BETA_SUPABASE_JWT_ISSUER=https://<project-ref>.supabase.co/auth/v1
BETA_SUPABASE_JWT_AUDIENCE=authenticated
```

The module deliberately refuses to authenticate if any setting is absent,
malformed, or if the issuer does not match the configured Supabase project.

## Route integration (later)

When beta routes are introduced, require the dependency explicitly:

```python
from fastapi import Depends
from jobagent.beta.auth import AuthenticatedUser, get_authenticated_user

@router.get("/beta/me")
async def me(user: AuthenticatedUser = Depends(get_authenticated_user)):
    return {"id": str(user.id), "email": user.email}
```

Use `user.id` as the ownership boundary for every database query. The client
must send a Supabase access token only as `Authorization: Bearer <token>`.
Do not accept a user ID supplied by the client as an ownership substitute.
