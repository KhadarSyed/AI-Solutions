# teams-mcp bug: `mail_send` / `mail_reply` fail — sender user id resolves to `undefined`

**Server:** `https://teams-mcp-emrk.onrender.com/mcp`
**Severity:** high — all outbound mail is broken (inbound/reads are fine)
**Regression:** started after the recent "no id returned resolved on mcp server end" change; outbound previously delivered.
**Observed:** 2026-07-22, via the production MCP client (same durable OAuth token used for reads).

## Summary

`mail_send` and `mail_reply` return errors that indicate the **sending user id is not being
resolved** — it comes through as the literal string `undefined`. Every **read** tool works
with the exact same token/session, so this is not auth and not the token; it's the send
handlers deriving the mailbox/user id from the wrong place.

## What works (reads — same token, same session)

| Tool | Result |
|---|---|
| `me_get` | ✅ `{"id":"399e9a46-40ba-4b47-bc09-a1b0dfbd04f3","mail":"InfoVision.Agent1317@alphametricx.com","userPrincipalName":"InfoVision.Agent1317@alphametricx.com"}` |
| `mail_list` | ✅ returns the inbox (messages with valid ids) |
| `get_pending_mentions` | ✅ returns results (empty list when none) |

So the delegated user **is** resolvable — `me_get` returns a valid id/UPN.

## What fails (sends)

| Call | Response |
|---|---|
| `mail_send {to:"khadar.syed@infovision.com", subject:"…", body:"…", contentType:"Text"}` | ❌ `{"text":"Id is malformed."}` |
| `mail_send {to:["khadar.syed@infovision.com"], subject:"…", body:"…"}` | ❌ `{"text":"Id is malformed."}` |
| `mail_send {from:"InfoVision.Agent1317@alphametricx.com", to:"…", subject:"…", body:"…"}` | ❌ `{"text":"The requested user 'undefined' is invalid."}` |
| `mail_reply {messageId:"<valid id from mail_list>", comment:"…"}` | ❌ `{"text":"Id is malformed."}` |

## Diagnosis

- The `from:"…"` case returning **`The requested user 'undefined' is invalid.`** is the tell:
  even with a valid `from`, the handler ends up with the sender = `undefined`. The `from`
  parameter is being ignored, and the delegated fallback isn't resolving the signed-in user.
- **`Id is malformed.`** on `mail_reply` is about the **user id in the request path, not the
  `messageId`** — the `messageId` passed was a valid id straight from `mail_list`. The user id
  segment is `undefined`, so Graph rejects the URL.

Root cause: the Graph request is almost certainly built as
`POST /users/{userId}/sendMail` (and `POST /users/{userId}/messages/{messageId}/reply`) with
`{userId}` = `undefined`.

## Suggested fix

In the `mail_send` / `mail_reply` handlers, resolve the sender the same way the read tools do:

1. **Delegated (no `from`):** call `POST /me/sendMail` and `POST /me/messages/{messageId}/reply`
   (the `/me` alias needs no id) — or resolve the id via the same code path `me_get` uses.
2. **Explicit `from`:** actually read the `from` argument and use it as `{userId}`
   (`/users/{from}/sendMail`) instead of whatever variable is currently `undefined`.
3. Add a guard: if the resolved sender id is falsy/`"undefined"`, fail fast with a clear message
   rather than letting `"undefined"` reach Graph.

## Quick verification after the fix

```
mail_send {to:"<your address>", subject:"send test", body:"ok", contentType:"Text"}
# expect: a success payload containing the sent message id (not "Id is malformed")
```

Once this returns a real id, the PR Intelligence agent's email delivery (threaded replies +
CC) will work end-to-end with no changes on the agent side.
