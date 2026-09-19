# RBAC Google Sheet sync — design

## Goal

Give the owner one always-current, read-only Google Sheet showing every menu, client surface and role and which roles can reach what, generated from the live RBAC configuration so it cannot drift.

Sheet: `1cI0FBBzF3k1V28aNnNRSNY4CUwElqXeC88gMkDoT--Y` (configured via env, not hardcoded).

## Scope

In: web console menus (`apps/identity/nav.py` `NAV_GROUPS`), mobile menus (parent tabs, teacher shell, POS kiosk), partner API scopes, roles and permission keys (`apps/identity/rbac.py`), platform roles.

"Device" = client surface (web console / mobile parent / mobile teacher / POS kiosk / partner API), not hardware device classes.

Out: per-user role assignments (no PII in the sheet), editing RBAC from the sheet, hardware device classes.

## Architecture

### `apps/identity/rbac_matrix.py` — pure generator
`build_matrix() -> dict[str, list[list[str]]]` (tab name to rows). Imports live `ROLE_PERMISSIONS`, `PLATFORM_ROLE_PERMISSIONS`, `NAV_GROUPS` and partner `ALLOWED_SCOPES`; holds no copy of any of them. Cell values are computed from the same permission-set membership `has_permission` uses.

Mobile menus declare no permissions in TSX, so a small explicit `MOBILE_MENUS` registry lives in this module (surface, label, roles/permission). A test parses `mobile/App.tsx` tab ids and fails when a tab is missing from the registry.

### Tabs (fully rewritten each sync)
1. **Access Matrix** — one row per menu x surface, one column per role (8 tenant roles + platform operator). Cell: `✓`, `✓*` (needs linked Staff profile), `✓†` (foundation admin only), blank.
2. **Roles x Permissions** — every permission key vs role.
3. **Menus** — surface, group, label, url name, permission key, extra gates, live vs coming-soon.
4. **Surfaces** — surface, who can use it (roles or partner scopes).
5. **Meta** — synced-at, git SHA, content hash.

### `sync_rbac_sheet` management command
`CronHostCommand`. Builds the matrix, hashes it, compares against the hash in the Meta tab; unchanged means no write. Otherwise clears and rewrites tabs via the Sheets API (`google-api-python-client` + `google-auth`, service account).

Env: `RBAC_SHEET_ID`, `RBAC_SHEET_SERVICE_ACCOUNT_JSON`. Unset means the command exits 0 with a clear "not configured" message so dev/CI are unaffected.

Scheduled hourly in `deploy/crontab` (next unique `sleep` stagger) and run after each deploy.

## Prerequisites (owner)
Create a Google service account, enable the Sheets API, share the sheet with the service account email as Editor, set both env vars on prod.

## Tests
- Generator: known role/menu cells, `✓*` / `✓†` markers, every `NAV_GROUPS` item appears, every `ROLE_PERMISSIONS` key appears.
- Mobile registry guard vs `mobile/App.tsx`.
- Command: mocked Sheets client; no write when hash unchanged; writes when changed; clean exit when unconfigured.
