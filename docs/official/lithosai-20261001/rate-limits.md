> ## Documentation Index
> Fetch the complete documentation index at: https://docs.lithosai.com/llms.txt
> Use this file to discover all available pages before exploring further.

# Rate Limits

Your organization has three per-minute budgets for each model: requests, input tokens and output tokens. Input and output are counted separately, so a large prompt and a long generation draw down different budgets.

Every key in an organization draws on the same rate limits and the same credit balance, so adding keys separates attribution, not budget.

Each budget refills continuously rather than resetting on a clock, and its capacity is set separately from its per-minute rate. So the remaining figures are not your limit minus what you have spent this minute. Read them as a balance rather than a countdown.

Exhausting one returns 429 with code `rate_limit_exceeded`, and `error.type` names the budget that refused: `requests`, `input_tokens` or `output_tokens`.

## Headers

| Header | Meaning |
| - | - |
| `x-ratelimit-limit-requests` | The capacity of your request bucket. |
| `x-ratelimit-remaining-requests` | Requests left in it. |
| `x-ratelimit-reset-requests` | How long until that bucket is full again. |
| `x-ratelimit-limit-tokens` | The capacity of whichever token bucket is nearer its limit. |
| `x-ratelimit-remaining-tokens` | Tokens left in it. |
| `x-ratelimit-reset-tokens` | How long until it is full again. |
| `retry-after` | Seconds to wait, on a refusal where waiting helps. |
| `retry-after-ms` | The same advice in milliseconds, and the more precise of the two. |
| `x-should-retry` | Set to false where no amount of waiting will change the answer. |

The rate limit headers accompany any response where your budgets were consulted, which is every admitted request and every rate limit refusal.

Retry 429 and 5xx with exponential backoff and jitter. Prefer the delay we advise, `retry-after-ms` first and then `retry-after`, over an interval of your own. Do not retry 400, 401, 402 or 404, and do not retry anything carrying `x-should-retry: false`: the answer will not change until you do something about it.

Your current limits are on the [Limits](https://console.lithosai.cloud/limits) page. Get in touch to raise them.
