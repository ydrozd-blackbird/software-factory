# Claude and Codex models for cross review

This page helps you pick models for cross review. All values are API list prices in USD per million tokens, read from the vendor pages on 2026-11-12. Prices change, so check the vendor page before you rely on them. Subscription logins use plan limits, not per-token bills.

## Models

Cached input is what OpenAI calls it; Anthropic calls it a cache read ("hits and refreshes"). Every model below has a 1M or 1.05M context window and 128K max output.

| Provider | Model | Model id | Input / output | Cached input | Context | Long prompts | Source |
|---|---|---|---|---|---|---|---|
| OpenAI | GPT-6 Astra | `gpt-6-astra` | $10 / $50 | $1.00 | 1.05M | Over 272K input: 2x input and cache, 1.5x output | [model page](https://developers.openai.com/api/docs/models/gpt-6-astra) |
| OpenAI | GPT-6.1 Sol | `gpt-6.1-sol` | $2 / $10 | $0.10 | 1.05M | Over 272K input: 2x input and cache, 1.5x output | [model page](https://developers.openai.com/api/docs/models/gpt-6.1-sol) |
| OpenAI | GPT-6.1 Terra | `gpt-6.1-terra` | $1 / $5 | $0.05 | 1.05M | Over 272K input: 2x input and cache, 1.5x output | [model page](https://developers.openai.com/api/docs/models/gpt-6.1-terra) |
| OpenAI | GPT-6 Luna | `gpt-6-luna` | $0.10 / $0.50 | $0.01 | 1.05M | Over 272K input: 2x input and cache, 1.5x output | [model page](https://developers.openai.com/api/docs/models/gpt-6-luna) |
| Anthropic | Claude Fable 5.2 | `claude-fable-5-2` | $8 / $40 | $0.20 | 1M | Same price at any length | [overview](https://platform.claude.com/docs/en/about-claude/models/overview), [pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| Anthropic | Claude Opus 5.5 | `claude-opus-5-5` | $4 / $20 | $0.20 | 1M | Same price at any length | [overview](https://platform.claude.com/docs/en/about-claude/models/overview), [pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| Anthropic | Claude Sonnet 5.5 | `claude-sonnet-5-5` | $2 / $10 | $0.10 | 1M | Same price at any length | [overview](https://platform.claude.com/docs/en/about-claude/models/overview), [pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| Anthropic | Claude Haiku 5.5 | `claude-haiku-5-5` | $0.10 / $0.50 | $0.01 | 1M | Prompt over 100K: $0.50 / $2.50, cached $0.05 | [overview](https://platform.claude.com/docs/en/about-claude/models/overview), [pricing](https://platform.claude.com/docs/en/about-claude/pricing) |

In Codex and ChatGPT Work, GPT-6.1 Sol uses a 500K context window by default, and subscription usage has no long-context surcharge (from the Sol page).

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
- The ledger does not model the OpenAI over-272K tier, because Codex reports tokens per turn, not per prompt. Costs of very long Codex prompts are too low.
- `codex exec` accepted `gpt-6.1-sol` on 2026-11-12 (codex-cli 0.166.0-alpha.6).

See [the cross review page](../skills/software-factory/references/cross-provider.md).
