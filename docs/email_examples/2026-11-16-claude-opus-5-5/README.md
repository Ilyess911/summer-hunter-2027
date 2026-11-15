# Application email evaluation: Claude Opus 5.5 (16 Nov 2026)

| Case | Result | Seconds | Attempts | Words | Clichés | CV 7-gram overlap | Cost (USD) |
|---|---|---|---|---|---|---|---|
| A FCCS Singapore | Opus | 17 | 1 | 237 | 0 | 0 | 0.044 |
| B CCI France UAE Dubai | blocked → free email (false positive « 3 à 5 mois », fixed) | 32 | 2 | 176 | 1 | 0 | 0.094 |
| C Airbus Business Development | Opus | 14 | 1 | 239 | 0 | 0 | 0.038 |
| D Robotics startup | Opus (business goal forced in, fixed) | 29 | 2 | 256 | 0 | 3 | 0.081 |
| E Strategy & Operations | Opus | 16 | 1 | 230 | 0 | 0 | 0.045 |
| F Published offer | Opus | 31 | 2 | 252 | 0 | 1 | 0.090 |
| G Executive contact | Opus | 17 | 1 | 164 | 0 | 0 | 0.044 |
| H Company change (Google Cloud) | Opus | 20 | 1 | 250 | 0 | 0 | 0.049 |
| J No verified company information | Opus | 14 | 1 | 247 | 0 | 0 | 0.038 |

Total: 0.523 USD for 9 cases (about 0.05 USD per email, 0.04 when no revision is needed).
Before (previous deterministic engine): ~205 words, CV 7-gram overlap 30-40 per email (bullets copied).
Each case file has the email before/after, the model's strategy answers and a rubric for human review.
