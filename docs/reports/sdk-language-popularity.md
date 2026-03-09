# SDK Language Popularity: OpenAI vs ElevenLabs (March 2026)

## Summary

**Contrary to the hypothesis, Python dominates both OpenAI and ElevenLabs SDK ecosystems by a wide margin.** TypeScript/JS is a solid second for OpenAI but trails significantly behind Python. For ElevenLabs, Python is even more dominant relative to their other SDKs.

## OpenAI SDKs

| Language | GitHub Stars | Downloads | Notes |
|---|---|---|---|
| **Python** | **30,200** | **~134M/month** (PyPI) | Dominant by every metric |
| **TypeScript/JS** | 10,700 | ~41M/month (npm) | Strong second, 8,962 npm dependents |
| **Go** (community) | 10,000 | N/A | `sashabaranov/go-openai`, predates official |
| **Go** (official) | 3,000 | N/A | Newer, growing |
| **.NET** | 2,524 | ~59K/day (NuGet) | 32.6M total NuGet downloads |
| **Java/Kotlin** | 1,374 | N/A (Maven) | Official SDK written in Kotlin |
| **Ruby** | 413 | N/A | Newest official SDK |

### Key Observations — OpenAI
- Python has **3x the stars** and **3x the monthly downloads** vs TypeScript/JS
- The Go community SDK (`sashabaranov/go-openai`) at 10K stars significantly outpaces the official Go SDK at 3K stars
- .NET has substantial NuGet adoption (32.6M total) despite fewer GitHub stars
- Java SDK is relatively new and still growing

## ElevenLabs SDKs

| Language | GitHub Stars | Downloads | Notes |
|---|---|---|---|
| **Python** | **2,900** | **~5M/month** (PyPI) | Overwhelmingly dominant |
| **TypeScript/JS** (Node) | 397 | N/A | Official Node library |
| **TypeScript** (Agents) | 86 | N/A | Agents SDK, ~1,100 dependents |
| **Swift** | 105 | N/A | Conversational AI / Agents focus |
| **Kotlin/Android** | 19 | N/A | Agents focus, very new |

### Key Observations — ElevenLabs
- Python is **7x more popular** on GitHub stars than the next SDK (TypeScript/JS at 397)
- ElevenLabs has **two TypeScript packages**: the general Node SDK (`elevenlabs-js`, 397 stars) and the Agents SDK (`packages`, 86 stars)
- Mobile SDKs (Swift, Kotlin) are focused on the Agents/Conversational AI platform, not general TTS/STT
- The overall ElevenLabs ecosystem is much smaller than OpenAI's (2.9K vs 30K stars for Python)

## Comparison: Python vs TypeScript/JS

| Metric | OpenAI Python | OpenAI TS/JS | Ratio | ElevenLabs Python | ElevenLabs TS/JS | Ratio |
|---|---|---|---|---|---|---|
| GitHub Stars | 30,200 | 10,700 | **2.8x** | 2,900 | 397 | **7.3x** |
| Monthly Downloads | 134M | 41M | **3.3x** | 5M | N/A | — |

## Implications for Dalston

Given that Dalston offers an ElevenLabs-compatible API:

1. **Python SDK users are the primary audience** — both OpenAI and ElevenLabs ecosystems are Python-first
2. **TypeScript/JS is a meaningful secondary audience** for OpenAI-compatible endpoints but less so for ElevenLabs-compatible ones
3. **Mobile SDKs** (Swift/Kotlin) exist for ElevenLabs but are focused on the Conversational AI agents platform rather than batch transcription
4. **Go and .NET** have notable OpenAI SDK adoption but no significant ElevenLabs equivalent

### Recommendation

The existing Python-first approach for Dalston's SDK/client library is well-aligned with market reality. If expanding SDK support, TypeScript/JS would be the clear next priority given its second-place position in both ecosystems.

## Sources

- [openai/openai-python — GitHub](https://github.com/openai/openai-python) (30.2K stars)
- [openai/openai-node — GitHub](https://github.com/openai/openai-node) (10.7K stars)
- [openai/openai-go — GitHub](https://github.com/openai/openai-go) (3K stars)
- [openai/openai-dotnet — GitHub](https://github.com/openai/openai-dotnet) (2.5K stars)
- [openai/openai-java — GitHub](https://github.com/openai/openai-java) (1.4K stars)
- [openai/openai-ruby — GitHub](https://github.com/openai/openai-ruby) (413 stars)
- [sashabaranov/go-openai — GitHub](https://github.com/sashabaranov/go-openai) (10K stars)
- [elevenlabs/elevenlabs-python — GitHub](https://github.com/elevenlabs/elevenlabs-python) (2.9K stars)
- [elevenlabs/elevenlabs-js — GitHub](https://github.com/elevenlabs/elevenlabs-js) (397 stars)
- [elevenlabs/packages — GitHub](https://github.com/elevenlabs/packages) (86 stars, TS Agents SDK)
- [elevenlabs/elevenlabs-swift-sdk — GitHub](https://github.com/elevenlabs/elevenlabs-swift-sdk) (105 stars)
- [elevenlabs/elevenlabs-android — GitHub](https://github.com/elevenlabs/elevenlabs-android) (19 stars)
- [openai — PyPI](https://pypi.org/project/openai/) (~134M monthly downloads)
- [openai — npm](https://www.npmjs.com/package/openai) (~41M monthly downloads)
- [elevenlabs — PyPI](https://pypi.org/project/elevenlabs/) (~5M monthly downloads)
- [OpenAI — NuGet](https://www.nuget.org/packages/OpenAI) (32.6M total downloads)
