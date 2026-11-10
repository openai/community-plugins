# Synthetic change brief

The public `listWidgets` API returns a response consumed by existing clients.
Add an optional `limit` parameter, defaulting to 10. It accepts integers from 1
to 100; invalid values must throw `RangeError`. Existing callers that omit the
parameter must continue to work without modification. No API version change or
client migration is part of this request.

Compare [before/api.mjs](before/api.mjs) with [after/api.mjs](after/api.mjs).
[consumer.mjs](consumer.mjs) represents an existing client. The records and
identifiers in these files are invented. The consumer should continue to return
`["Alpha", "Beta"]` for the default response and `["Alpha"]` for `limit: 1`.

The implementation may use an inline finite integer check. Do not require a
new validation abstraction or different private variable names solely for style.
Keep the review read-only and return findings in chat.
