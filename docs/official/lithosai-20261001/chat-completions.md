> ## Documentation Index
> Fetch the complete documentation index at: https://docs.lithosai.com/llms.txt
> Use this file to discover all available pages before exploring further.

# Create a chat completion

> Generates a completion for the supplied conversation.

Set `stream: true` to receive Server-Sent Events. Most chunks carry
a `usage` object: the opening role delta and the chunk carrying `finish_reason` omit
it. The final chunk before `[DONE]` reports the totals with an empty `choices`
array.

Sampling constraints are **per model**. The bounds in this schema are the OpenAI-wire
bounds, not the per-model ones.

Retry 429 and 5xx with exponential backoff and jitter. Prefer the delay we advise,
`retry-after-ms` first and then `retry-after`, over an interval of your own. Do not
retry 400, 401, 402 or 404, and do not retry anything carrying `x-should-retry: false`:
the answer will not change until you do something about it.

A 429 means one of your three per-minute budgets is empty. See
[Rate limits](/rate-limits) for what they are and how to read the headers that
report them.




## API Specification

The full API specification for this endpoint is available in the [documentation index](https://docs.lithosai.com/llms.txt).
