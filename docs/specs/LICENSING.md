# Dalston Licensing Model

## Current License: Apache 2.0

Dalston is released under the **Apache License 2.0**, a permissive open-source license.

### What This Means

| Permitted | Required | Not Permitted |
|-----------|----------|---------------|
| ✅ Commercial use | ⚠️ License notice | |
| ✅ Modification | ⚠️ State changes | |
| ✅ Distribution | | |
| ✅ Patent use | | |
| ✅ Private use | | |

You can use Dalston for anything: personal projects, commercial products, internal tools, hosted services.

### Why Apache 2.0

- **Patent grant**: Explicit protection against patent claims from contributors
- **Permissive**: Maximum adoption, minimum friction
- **Enterprise-friendly**: Approved by most corporate legal teams
- **Industry standard**: Used by Kubernetes, TensorFlow, many CNCF projects

---

## Contributor License Agreement (CLA)

**All contributions require a signed CLA before merge.**

### Why We Require a CLA

1. **Legal clarity**: Confirms contributors have the right to contribute the code
2. **Future flexibility**: Allows relicensing future versions if necessary
3. **Patent protection**: Contributors grant patent rights for their contributions

### CLA Process

We use [CLA Assistant](https://cla-assistant.io/), a free GitHub integration:

1. **First-time contributors**: When you open your first PR, CLA Assistant bot comments with a link
2. **Sign once**: Click the link, sign in with GitHub, review and accept the CLA
3. **Automatic tracking**: Future PRs are automatically approved (no re-signing needed)
4. **PR blocked until signed**: PRs cannot be merged without a valid CLA signature

### CLA Terms (Summary)

- You grant Dalston Authors a perpetual, worldwide, royalty-free license to use, modify, and distribute your contribution
- You confirm you have the right to make the contribution (you wrote it, or have permission)
- You grant patent rights for any patents covering your contribution
- Original copyright remains with you
- Dalston Authors may relicense your contribution under a different license in the future

Full CLA text: [docs/legal/CLA.md](../../legal/CLA.md)

### Alternative: Developer Certificate of Origin (DCO)

For minor contributions (typos, docs, small fixes), we may accept a DCO sign-off instead of full CLA:

```bash
git commit -s -m "Fix typo in README"
# Adds: Signed-off-by: Your Name <email@example.com>
```

The DCO confirms you have the right to submit the code under the project's license.

---

## License Headers

All source files should include:

```python
# Copyright 2024 Dalston Authors
# SPDX-License-Identifier: Apache-2.0
```

---

## Future Considerations

The project may in the future:

1. **Add commercial features** in a separate package (e.g., `dalston-pro`) under a commercial license
2. **Change the license** for future versions if needed to protect the project

Examples of why this might happen:
- Cloud providers offering Dalston-as-a-service without contributing back
- Need to fund development through commercial offerings
- Enterprise features requiring different licensing terms

**Important guarantees:**
- Previously released versions remain under their original license forever
- The CLA enables these options while protecting contributor rights
- Any license change would be communicated clearly with rationale

### Architectural Preparation

The codebase uses **Protocol-based extension points** that support future commercial add-ons without fragmenting the core:

```python
# Core defines the interface
class ConsoleAuthProvider(Protocol):
    def get_auth_status(self, request: Request) -> AuthStatus: ...
    async def get_current_user(self, request: Request) -> ConsoleUser | None: ...

# Core ships a default implementation (NoopAuthProvider)
# Future packages can provide alternatives without modifying core
```

This pattern allows optional features to plug in cleanly.

---

## FAQ

### Can I use Dalston for commercial purposes?

Yes. Apache 2.0 allows commercial use without payment or royalties.

### Can I modify Dalston and keep my changes private?

Yes. Apache 2.0 does not require you to share modifications (unlike AGPL/GPL).

### Can I offer Dalston as a hosted service?

Yes, currently. This may change in future versions if the project adopts a more restrictive license.

### Why require a CLA instead of just accepting contributions?

The CLA provides legal clarity and future flexibility. Without it, relicensing would require permission from every contributor. Companies like Apache Foundation, Google, and Microsoft require CLAs for similar reasons.

### What if I don't want to sign the CLA?

You can still:
- Use Dalston freely under Apache 2.0
- Fork and maintain your own version
- Report issues and suggest features
- Contribute documentation with DCO sign-off only

### Will you change the license to something restrictive?

We have no current plans to do so. If circumstances require it (e.g., to sustain development), we would:
- Communicate clearly and early
- Only apply changes to new versions
- Preserve Apache 2.0 for all prior releases
