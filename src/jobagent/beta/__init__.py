"""Multi-user beta foundations.

This package is intentionally isolated from the founder's single-user API. It
contains reusable authentication and request-schema building blocks only; beta
routes should opt in explicitly when they are introduced.

Imports are deliberately not re-exported here. In particular, this allows
schema-only consumers to work before the optional JWT runtime dependency is
installed. Import authentication primitives from ``jobagent.beta.auth``.
"""
