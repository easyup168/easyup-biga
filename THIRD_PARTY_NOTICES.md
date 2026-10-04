# EasyUp for BigA 2.0 Third-Party Notices

This file tracks third-party software, adapted source code, bundled assets,
and other works actually distributed with EasyUp for BigA 2.0.

> Do not list a project here merely because it was researched or mentioned in
> documentation. Record components actually copied, modified, bundled,
> redistributed, or required to be attributed by their license.

## Current inventory

Audited 2026-09-23 against the actual repository. `pyproject.toml` declares
no `[project.dependencies]` and there is no lock file, so this was derived by
AST-scanning every tracked `.py` file for top-level imports and checking each
non-stdlib, non-EasyUp-for-BigA-2.0-internal name against the environment's installed
package metadata (`importlib.metadata`) — not from a lock file, since none
exists yet.

**No third-party source code is copied, adapted, vendored, or bundled** in
this repository (checked `skills/`, `src/`, `bin/`, `tools/`, `deploy/` for
common markers — "adapted from", "vendored", "based on ... license", inline
third-party copyright headers — none found).

Two third-party Python packages are actually imported by EasyUp for BigA 2.0's own code,
recorded here in the required record format:

| Field | PyYAML | pytest |
|---|---|---|
| Component | `PyYAML` | `pytest` |
| Version / commit | `6.0.3` | `9.0.3` |
| Source | https://pypi.org/project/PyYAML/ | https://pypi.org/project/pytest/ |
| License | `MIT` | `MIT` |
| Usage | Runtime dependency — `deploy/openclaw/apply_config.py` reads/writes OpenClaw's YAML config | Development/test-only — imported exclusively under `tests/`; not distributed with EasyUp for BigA 2.0, never imported by the actual pipeline (`bin/biga-card`, `bin/biga-notify`, etc.) |
| Modified | No — installed from PyPI as-is | No — installed from PyPI as-is |
| Required notice | Standard MIT copyright notice, carried in the installed package's own metadata; not reproduced here since EasyUp for BigA 2.0 does not bundle or redistribute the package source | Same as PyYAML |
| License location | PyPI package metadata (`License-Expression: MIT`, `License-File: LICENSE` in the installed distribution) | Same as PyYAML |

Everything else EasyUp for BigA 2.0's own code imports is either the Python standard
library (`urllib.request`, `http.client`, `sqlite3`, `json`, `dataclasses`,
`ast`, …) or EasyUp for BigA 2.0's own internal packages (`_contract`, `_store`, `_sources`,
`_runtime`, `_snapshot`). This project deliberately avoids third-party
HTTP/data-wrapper libraries for its provider adapters — see
`src/easyup_biga/providers/eastmoney.py`'s docstring for the concrete incident
that motivated that choice.

**OpenClaw** (the multi-agent runtime this project runs on) is MIT licensed
(`~/.openclaw-biga/runtime/node_modules/openclaw/{package.json,LICENSE}`),
but it is a **separate, independently installed peer runtime**
(`npm i --prefix ~/.openclaw-biga/runtime openclaw@latest`; never `git`-tracked
inside this repository) — not bundled, vendored, or redistributed by EasyUp for BigA 2.0's
own source tree. Its own dependency tree is that project's own notice, not
this one's.

⚠️ This repository still has no formal dependency declaration or lock file.
Before a formal release, add one (`[project.dependencies]` or equivalent) —
otherwise every future audit has to re-derive this table by scanning source,
which silently misses anything imported only inside a code path a static
scan doesn't reach (for example, an optional dependency imported lazily
inside a function body only reached under specific runtime conditions).

## Required record format

| Field | Value |
|---|---|
| Component | `<project/package name>` |
| Version / commit | `<version or commit>` |
| Source | `<upstream repository or project URL>` |
| License | `<SPDX identifier>` |
| Usage | `<dependency / adapted code / bundled asset / etc.>` |
| Modified | `<yes/no; describe if yes>` |
| Required notice | `<copyright/NOTICE text if required>` |
| License location | `<path or URL>` |

## Data-provider notice

EasyUp for BigA 2.0's software license does **not** grant rights to market data, financial
data, news, research reports, filings, web content, or other third-party data
retrieved through EasyUp for BigA 2.0.

Each provider remains subject to its own terms, licenses, access restrictions,
rate limits, attribution requirements, and redistribution rules — none of
which have been formally reviewed. The table below records what is
verifiable from EasyUp for BigA 2.0's own adapter source code today; it is not a legal
review of any provider's terms of service.

| Provider | Interface | Auth in adapter code | ToS / rate-limit reviewed |
|---|---|---|---|
| 深交所 SZSE `monthList`（`src/easyup_biga/providers/szse.py`） | **Official** exchange endpoint | None | Not reviewed |
| 新浪财经日线（`src/easyup_biga/providers/sina.py`） | Unofficial, reverse-engineered public endpoint | None | Not reviewed |
| 新浪财经 7x24 快讯（`src/easyup_biga/providers/sina_news.py`） | Unofficial, reverse-engineered public endpoint | None | Not reviewed |
| 腾讯行情（`src/easyup_biga/providers/tencent.py`） | Unofficial, reverse-engineered public endpoint | None | Not reviewed |
| 东方财富股池/涨跌家数（`src/easyup_biga/providers/eastmoney.py`） | Unofficial, reverse-engineered public endpoint | None | Not reviewed |

"Auth in adapter code: None" means EasyUp for BigA 2.0's own code sends no API key or
token to that endpoint — it is **not** a claim that the provider's terms
permit unauthenticated programmatic access; that determination has not been
made for any of the five.

Provider adapters should document, at minimum:

- provider/source name;
- official or unofficial interface status;
- authentication requirements;
- rate-limit policy;
- redistribution restrictions;
- commercial-use restrictions, if known;
- date the terms were last reviewed.

## Reference projects

Projects researched as architectural references are **not automatically
third-party components of EasyUp for BigA 2.0**. Do not copy their code until the applicable
license has been reviewed and all required attribution has been added.
