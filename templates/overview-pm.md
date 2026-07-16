# {{project_name}} — 产品视角概览 (PM Overview)

> This report is derived from **repository evidence only** — code, configuration, and
> git history in the analyzed snapshot (clean commit, dirty worktree, or non-git folder,
> as recorded in the provenance block). It does not observe production traffic, runtime
> performance, or incident history; it cannot confirm whether a configured integration
> is live in production; and it is not a comprehensive security or license audit.
> Findings labeled `status unresolved` need human confirmation.
<!-- For zh-CN runs, render the disclaimer as its faithful zh-CN translation (same
scope claims). This document is the NON-TECHNICAL companion to overview.md: business
language only — no tool names, no metric jargon, no code identifiers except verbatim
UI labels and (parenthesized) module IDs. Every claim still traces to overview.md /
project_map.md; link, don't re-argue. -->

- **运行**: `{{run_id}}` · {{analyzed_at}} · 详细技术报告: [`overview.md`](overview.md)

## 这个系统是什么

{{three_to_five_sentences_in_product_language: what the product does, for whom, and
what it is made of at repo/service granularity — no architecture jargon}}

## 它由哪些业务模块组成

| 模块 | 它做什么 | 状态一句话 |
|---|---|---|
| {{business_module_name (`module-id`)}} | {{what_it_does_for_users}} | {{one_plain_sentence — healthy / needs attention / at risk, mirroring the health table}} |

<!-- business modules only; platform/shared-infra roll into one closing row -->

## 最需要关注的事 (Top risks, in plain language)

{{3_to_6_items; for each: what could go wrong in USER/BUSINESS terms, why we believe
it (one sentence, no metrics), and what kind of decision or investment would address
it. Link each to its overview.md problem number.}}

## 系统对外依赖什么

{{plain-language list of the `included` external systems from the disposition table —
what the product relies on (storage, mail, chat, issue tracker, ...); note anything
`unresolved` as "存在迹象但未确认"}}

## 需要产品/团队回答的问题

{{the assumptions & open questions that need a HUMAN decision, phrased for a PM;
each with why it matters in product terms}}

## 这份报告没有覆盖什么

{{honest plain-language coverage summary: what the analysis could not see (e.g.
production behavior, un-analyzed backends, skipped scans) — mirroring the lens
coverage table without its jargon}}
