# BigA Open-Source Release Checklist

> 🔧 **操作** · 跑不通就是错的
> **覆盖**：正式对外发布前要核对的许可/三方/数据/保密/交易安全清单 ｜
> **不覆盖**：具体条款怎么写（见 `LICENSE`/`NOTICE`/`THIRD_PARTY_NOTICES.md` 本身）

---

## Repository licensing

- [ ] Root `LICENSE` contains the Apache License 2.0 text.
- [ ] Root `NOTICE` is present and current.
- [ ] README contains the license/data/disclaimer section.
- [ ] BigA-owned source files follow the chosen SPDX/header convention.
- [ ] No BigA Apache header has been added to third-party source files.

## Third-party software

- [ ] Dependency lock files have been reviewed.
- [ ] Bundled/copied/adapted source has been identified.
- [ ] Required upstream copyright notices are preserved.
- [ ] Required licenses are distributed where necessary.
- [ ] Required NOTICE/attribution text is included.
- [ ] `THIRD_PARTY_NOTICES.md` reflects what is actually distributed.
- [ ] Noncommercial, copyleft, proprietary, or source-available components
      received explicit review before inclusion.

## Data and content

- [ ] Provider terms have been reviewed for production use.
- [ ] Redistribution rights are known before shipping sample datasets.
- [ ] No licensed market-data dump is committed accidentally.
- [ ] No proprietary research/news content is bundled without permission.
- [ ] Provider adapters document rate limits and known usage restrictions.

## Secrets and privacy

- [ ] No API keys, broker secrets, tokens, cookies, or private certificates.
- [ ] No account IDs or private portfolio exports.
- [ ] Git history has been checked for leaked secrets.
- [ ] Example configuration uses placeholders only.

## Trading safety

- [ ] Live broker functionality is separated from paper/simulation.
- [ ] Risk checks cannot be bypassed by Agent output.
- [ ] Kill-switch behavior is tested.
- [ ] Order idempotency is tested.
- [ ] Financial disclaimer is visible in project documentation.

## Release provenance

- [ ] Release tag identifies the exact source.
- [ ] Generated artifacts can be reproduced from tagged source.
- [ ] Third-party versions/commits are recorded.
- [ ] LICENSE/NOTICE contents match the actual release contents.
