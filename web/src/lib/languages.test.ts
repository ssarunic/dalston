import { describe, expect, it } from 'vitest'
import {
  AUTO_LANGUAGE,
  COMMON_LANGUAGES,
  buildLanguageOptions,
  getLanguageLabel,
  unionModelLanguages,
} from './languages'

describe('getLanguageLabel', () => {
  it('maps house codes to display names', () => {
    expect(getLanguageLabel('hr')).toBe('Croatian')
    expect(getLanguageLabel('en')).toBe('English')
    expect(getLanguageLabel('mt')).toBe('Maltese')
  })

  it('keeps the labels the batch page used to ship', () => {
    expect(getLanguageLabel('bn')).toBe('Bengali')
    expect(getLanguageLabel('ur')).toBe('Urdu')
    expect(getLanguageLabel('fa')).toBe('Persian')
    expect(getLanguageLabel('sw')).toBe('Swahili')
    expect(getLanguageLabel('is')).toBe('Icelandic')
  })

  it('resolves unmapped valid codes through Intl.DisplayNames', () => {
    expect(getLanguageLabel('tk')).toBe('Turkmen')
    expect(getLanguageLabel('ht')).toBe('Haitian Creole')
  })

  it('upper-cases codes nothing can name', () => {
    expect(getLanguageLabel('xx')).toBe('XX')
    expect(getLanguageLabel('not a tag!')).toBe('NOT A TAG!')
  })
})

describe('buildLanguageOptions', () => {
  it('uses the model card list, sorted by label, with auto-detect first', () => {
    const opts = buildLanguageOptions(['uk', 'hr', 'en'])
    expect(opts.map((o) => o.value)).toEqual([AUTO_LANGUAGE, 'hr', 'en', 'uk'])
    expect(opts[1].label).toBe('Croatian')
  })

  it('falls back to the common set for multilingual or missing lists', () => {
    const expected = [AUTO_LANGUAGE, ...COMMON_LANGUAGES].sort()
    for (const input of [null, undefined, [], ['*']]) {
      const values = buildLanguageOptions(input).map((o) => o.value)
      expect([...values].sort()).toEqual(expected)
    }
  })

  it('common set still covers everything the real-time page offered before', () => {
    for (const code of ['pl', 'uk', 'sv', 'da', 'fi', 'no', 'tr']) {
      expect(COMMON_LANGUAGES).toContain(code)
    }
  })

  it('drops the wildcard marker when it appears alongside real codes', () => {
    const values = buildLanguageOptions(['*', 'en']).map((o) => o.value)
    expect(values).not.toContain('*')
  })
})

describe('unionModelLanguages', () => {
  it('unions concrete lists', () => {
    const u = unionModelLanguages([{ languages: ['en', 'hr'] }, { languages: ['hr', 'de'] }])
    expect([...(u ?? [])].sort()).toEqual(['de', 'en', 'hr'])
  })

  it('adds the common set when any model is multilingual, keeping concrete codes', () => {
    const u = unionModelLanguages([{ languages: null }, { languages: ['mt'] }])
    expect(u).toContain('mt')
    for (const code of COMMON_LANGUAGES) expect(u).toContain(code)
  })

  it('returns null with no models so the builder uses the common set', () => {
    expect(unionModelLanguages([])).toBeNull()
  })
})
