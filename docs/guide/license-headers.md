# BigA License Header Guide

> 🔧 **操作** · 跑不通就是错的
> **覆盖**：新增 BigA 自有源文件时怎么写版权/许可头 ｜
> **不覆盖**：许可证本身怎么选（见 [`../../LICENSE`](../../LICENSE)）、
> 三方组件的记录规则（见 [`../../THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md)）

---

BigA uses Apache License 2.0 (`Apache-2.0`).

For BigA-owned source files, use the compact SPDX form where practical:

## Python / shell

```python
# Copyright 2026 BigA contributors
# SPDX-License-Identifier: Apache-2.0
```

## JavaScript / TypeScript

```text
// Copyright 2026 BigA contributors
// SPDX-License-Identifier: Apache-2.0
```

## YAML

```yaml
# Copyright 2026 BigA contributors
# SPDX-License-Identifier: Apache-2.0
```

Do not add this header to third-party source files. Preserve their original
copyright and licensing notices and record applicable redistributed works in
`THIRD_PARTY_NOTICES.md`.
