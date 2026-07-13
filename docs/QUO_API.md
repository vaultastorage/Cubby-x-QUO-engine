# Quo (OpenPhone) API — contacts

Base URL: `https://api.openphone.com/v1`
Auth: API key in the `Authorization` header. OpenPhone normally uses the **raw key**
(no `Bearer ` prefix). If calls 401, set `QUO_AUTH_SCHEME="Bearer "` in `.env`.
Rate limit: 10 requests/second per key.

## Endpoints used
- `POST /contacts` — create a contact. Returns an `id`. **Save it** — every future
  update needs it.
- `PATCH /contacts/:id` — update a contact.
- `GET /contacts/:id` — used only by `check` (a bogus id returns 404 = auth OK).

## Contact body shape
```json
{
  "defaultFields": {
    "firstName": "Kelly Soverns - 43",
    "phoneNumbers": [{ "name": "primary", "value": "+13055551234" }]
  }
}
```
Default fields: `firstName`, `lastName`, `role`, `company`, `emails`, `phoneNumbers`.
Convention here: the whole `Name - Unit` string goes in `firstName`, `lastName`
blank. Phones must be **E.164** (`+1XXXXXXXXXX`). Custom field *definitions* can only
be created in the Quo app, not via API, so this project stores the `customerId →
quo_id` map in `state.json` rather than a Quo custom field.

## Caveats that shaped the design
1. **The API only manages contacts it created.** All operations key off the `id`
   returned by `POST /contacts`. There is no bulk "list all contacts" to adopt
   contacts made in-app or by CSV import. Therefore the baseline must be created via
   the API so we capture each id — a CSV-imported baseline could not be patched and
   would duplicate on the first move-out.
2. **CSV import doesn't update.** Quo flags potential duplicates on import and leaves
   you to merge manually; it adds, it doesn't upsert. That's why the cutover is a
   manual wipe + API-created baseline, not a re-import.
3. **Visibility after a conversation.** An API-created card appears in the app's
   contact list / search once there's a conversation on a matching number. Existing
   tenants who've already called/texted re-surface immediately; a never-contacted
   tenant's card appears on their first contact (which is when you need it for caller
   ID anyway).

## What we do NOT use
No SMS/email send (those consume message credits and aren't part of contact sync).
No deletes from code — the one-time wipe is done by the user in the Quo app.
