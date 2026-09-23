# Contributing to EasyUp for BigA 2.0

Thank you for contributing to EasyUp for BigA 2.0.

## License of contributions

Unless you explicitly state otherwise, any contribution intentionally
submitted for inclusion in EasyUp for BigA 2.0 is submitted under the Apache License,
Version 2.0, without additional terms or conditions.

By submitting a contribution, you represent that you have the right to submit
it under those terms.

Do not submit code, datasets, model weights, prompts, documentation, images,
or other material copied from a third party unless its license permits the
intended use and all required notices are included.

## Before opening a pull request

1. Keep business workflow deterministic: Agents may make assessments, but
   orchestration, state transitions, risk gates, and persistence rules belong
   to EasyUp for BigA 2.0 code.
2. Do not add direct provider access to automated Agents. Data should pass
   through EasyUp for BigA 2.0 provider/data/snapshot contracts.
3. Do not add secrets, API keys, account identifiers, broker credentials, or
   private market-data files to the repository.
4. Add or update tests for behavioral changes.
5. Record copied/adapted/bundled third-party works in `THIRD_PARTY_NOTICES.md`.
6. Preserve upstream copyright, license, NOTICE, and attribution text when
   required.
7. Mark materially modified third-party files as modified when their license
   requires it.
8. Do not assume an open-source adapter makes its underlying data source
   freely redistributable.

## Source-file licensing

For new BigA-owned source files, the preferred compact header is:

```text
Copyright 2026 BigA contributors
SPDX-License-Identifier: Apache-2.0
```

Use the appropriate comment syntax for the file type. See
[`docs/guide/license-headers.md`](docs/guide/license-headers.md) for
per-language examples.

Do not add the BigA Apache header to third-party source files. Preserve the
third party's own notices and license terms.

## Financial-system safety

Changes involving broker execution, order management, account state, risk
limits, or automated trading should include deterministic tests and should
not bypass approval, risk, audit, or kill-switch boundaries.
