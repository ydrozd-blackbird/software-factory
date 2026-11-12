# Claude and Codex models for cross review

This page helps you pick models for cross review. Every price is an estimate, read on 2026-11-12. A price marked "unverified" has no fetched source. Do not treat it as confirmed. Prices change, so check the vendor page first.

## OpenAI models

Each row cites a page for that model. A value with no model page is marked "unverified". The ledger shows "estimate, price unverified" next to a cost computed from such a price.

| Model id | Input / output per 1M | Cached input | Context | Source |
|---|---|---|---|---|
| `gpt-6-astra` (id unverified) | $10 / $50 (unverified) | not listed | 1.05M (unverified) | No model page fetched. Only the list page was read: https://developers.openai.com/api/docs/models |
| `gpt-6.1-sol` | $2 / $10 | not listed | 1.05M | https://developers.openai.com/api/docs/models/gpt-6.1-sol |
| `gpt-6.1-terra` (id unverified) | $1 / $5 (unverified) | not listed | 1.05M (unverified) | No model page fetched. Only the list page was read: https://developers.openai.com/api/docs/models |
| `gpt-6-luna` | $0.10 / $0.50 | $0.01 | 1.05M | https://developers.openai.com/api/docs/models/gpt-6-luna |

Luna prompts over 272K input tokens cost 2x for input and 1.5x for output. This comes from the Luna page.

## Claude models

All Claude prices come from the Claude pricing page, which lists each model: https://platform.claude.com/docs/en/about-claude/pricing. This page gives no Claude context sizes. No Claude model id was fetched from a vendor page, so every id below is unverified. The pricing page is not a source for ids. Check the ids on the Claude models overview, https://platform.claude.com/docs/en/about-claude/models/overview, before you rely on them.

| Model | Model id | Input / output / cache read per 1M |
|---|---|---|
| Haiku 5.5 | `claude-haiku-5-5` (id unverified) | $0.10 / $0.50 / $0.01 up to 100K prompt; $0.50 / $2.50 / $0.05 over 100K |
| Sonnet 5.5 | `claude-sonnet-5-5` (id unverified) | $2 / $10 / $0.10 |
| Opus 5.5 | `claude-opus-5-5` (id unverified) | $4 / $20 / $0.20 |
| Fable 5.2 | id unverified | $8 / $40 / $0.20 |

## Roles

| Role | Single provider | Cross review |
|---|---|---|
| Reviewer | Opus critic and testers, as today | Codex `gpt-6.1-sol` at `high` |
| Fallback | Not used | Opus at `high`, labelled same-provider |

## Why mix providers

Some papers suggest that a reviewer from the same model family can share the author's blind spots. They measure judging and question answering, not code review.

- Panickssery, Bowman and Feng (NeurIPS 2024): models favor their own summaries. https://arxiv.org/abs/2404.13076
- Goel et al. (2025): judges favor models that make similar mistakes. https://arxiv.org/abs/2502.04313
- Preference Leakage (2025): judges favor related models. https://arxiv.org/pdf/2502.01534
- Choi et al. (NeurIPS 2025): majority voting explains most debate gains. https://neurips.cc/virtual/2025/poster/116557
- "Talk Isn't Always Cheap" (2025): debate can lower accuracy. https://arxiv.org/abs/2509.05396

We found no study of cross-family review of code that agents wrote. The idea that Opus reviewing Opus misses the same bugs fits these papers. It is not proven for code review.

For interactive use, OpenAI ships a plugin for Claude Code: `openai/codex-plugin-cc`, at https://github.com/openai/codex-plugin-cc.

## Limits of this page

- Prices are dated 2026-11-12. Subscription logins use plan limits, not per-token bills.
- Model access depends on your account.
- The Sol and Astra long-prompt tiers are unconfirmed.
- The ledger does not model Luna's long-prompt tier.
- `codex exec` accepted `gpt-6.1-sol` on 2026-11-12 (codex-cli 0.166.0-alpha.6).

See [the cross review page](../skills/software-factory/references/cross-provider.md).
