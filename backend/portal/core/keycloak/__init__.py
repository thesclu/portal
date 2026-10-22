"""The portal's side of sign-in on Keycloak (P25a; DECISIONS 51, 52).

`config`: where Keycloak is and the two clients' secrets. `oidc`: the authorization code flow as
an OpenID Connect client (Authlib), the logout token check. `sessions`: a portal session tied to
its Keycloak session (refresh, re-verification, back-channel logout, idle limit). `admin`: the
admin API (python-keycloak), which keeps Keycloak's copy of each person in line with the portal's.
`sync`: the queue of admin-API changes, each retried until Keycloak has it. `policy`: the sign-in
rules an administrator sets in Settings, written to Keycloak.
"""
