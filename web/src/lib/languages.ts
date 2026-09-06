/**
 * Language code → display name, plus the option-list builder shared by the
 * batch (New Job) and Real-time pages.
 *
 * Internal to the web/ package. The authoritative per-model language list
 * comes from the model registry (`ModelRegistryEntry.languages`); this file
 * only supplies human-readable labels and the fallback set shown when no
 * model is selected or the model is multilingual.
 */

/** House names for common codes; anything else resolves via Intl.DisplayNames. */
export const LANGUAGE_NAMES: Record<string, string> = {
  en: 'English',
  es: 'Spanish',
  fr: 'French',
  de: 'German',
  it: 'Italian',
  pt: 'Portuguese',
  nl: 'Dutch',
  ja: 'Japanese',
  ko: 'Korean',
  zh: 'Chinese',
  ar: 'Arabic',
  ru: 'Russian',
  hi: 'Hindi',
  pl: 'Polish',
  tr: 'Turkish',
  vi: 'Vietnamese',
  th: 'Thai',
  cs: 'Czech',
  ro: 'Romanian',
  hu: 'Hungarian',
  el: 'Greek',
  da: 'Danish',
  fi: 'Finnish',
  no: 'Norwegian',
  sv: 'Swedish',
  he: 'Hebrew',
  id: 'Indonesian',
  ms: 'Malay',
  uk: 'Ukrainian',
  bg: 'Bulgarian',
  ca: 'Catalan',
  hr: 'Croatian',
  sk: 'Slovak',
  sl: 'Slovenian',
  sr: 'Serbian',
  lt: 'Lithuanian',
  lv: 'Latvian',
  et: 'Estonian',
  ta: 'Tamil',
  te: 'Telugu',
  bn: 'Bengali',
  mr: 'Marathi',
  gu: 'Gujarati',
  kn: 'Kannada',
  ml: 'Malayalam',
  pa: 'Punjabi',
  ur: 'Urdu',
  fa: 'Persian',
  sw: 'Swahili',
  tl: 'Tagalog',
  af: 'Afrikaans',
  cy: 'Welsh',
  gl: 'Galician',
  eu: 'Basque',
  is: 'Icelandic',
  mt: 'Maltese',
  ga: 'Irish',
  sq: 'Albanian',
  mk: 'Macedonian',
  bs: 'Bosnian',
  az: 'Azerbaijani',
  kk: 'Kazakh',
  uz: 'Uzbek',
  mn: 'Mongolian',
  ne: 'Nepali',
  si: 'Sinhala',
  km: 'Khmer',
  lo: 'Lao',
  my: 'Burmese',
  ka: 'Georgian',
  am: 'Amharic',
  yo: 'Yoruba',
  zu: 'Zulu',
  jv: 'Javanese',
  su: 'Sundanese',
}

/**
 * Shown when nothing narrower is known: no model selected and no registry
 * data, or a multilingual model. Union of the sets both pages offered before
 * language lists were model-driven.
 */
export const COMMON_LANGUAGES = [
  'en', 'es', 'fr', 'de', 'it', 'pt', 'nl', 'ja', 'ko', 'zh', 'ar', 'ru', 'hi',
  'pl', 'uk', 'sv', 'da', 'fi', 'no', 'tr',
]

export const AUTO_LANGUAGE = 'auto'

export interface LanguageOption {
  value: string
  label: string
}

const intlDisplayNames: Intl.DisplayNames | null = (() => {
  try {
    return new Intl.DisplayNames(['en'], { type: 'language' })
  } catch {
    return null
  }
})()

export function getLanguageLabel(code: string): string {
  const house = LANGUAGE_NAMES[code]
  if (house) return house
  try {
    const intl = intlDisplayNames?.of(code)
    // Intl returns the input unchanged for codes it cannot name.
    if (intl && intl !== code) return intl
  } catch {
    // Malformed tag — fall through.
  }
  return code.toUpperCase()
}

function isMultilingual(languages: string[] | null | undefined): boolean {
  return !languages || languages.length === 0 || languages.includes('*')
}

/**
 * Union the language lists of several models (e.g. everything the gateway
 * could auto-select). Returns `null` if any model is multilingual, so the
 * caller falls back to the common set plus every concrete code seen.
 */
export function unionModelLanguages(
  models: ReadonlyArray<{ languages: string[] | null | undefined }>,
): string[] | null {
  if (models.length === 0) return null
  const codes = new Set<string>()
  let anyMultilingual = false
  for (const m of models) {
    if (isMultilingual(m.languages)) {
      anyMultilingual = true
    }
    for (const code of m.languages ?? []) {
      if (code !== '*') codes.add(code)
    }
  }
  if (anyMultilingual) {
    for (const code of COMMON_LANGUAGES) codes.add(code)
  }
  return codes.size > 0 ? [...codes] : null
}

/**
 * Build the language dropdown for a model.
 *
 * `modelLanguages` is the registry entry's list; `null`, empty, or a list
 * containing `*` means multilingual and yields the common fallback set.
 * Always starts with Auto-detect and sorts the rest by display name.
 */
export function buildLanguageOptions(
  modelLanguages: string[] | null | undefined,
): LanguageOption[] {
  const languages = isMultilingual(modelLanguages)
    ? COMMON_LANGUAGES
    : (modelLanguages as string[])
  return [
    { value: AUTO_LANGUAGE, label: 'Auto-detect' },
    ...languages
      .filter((l) => l !== '*')
      .sort((a, b) => getLanguageLabel(a).localeCompare(getLanguageLabel(b)))
      .map((code) => ({ value: code, label: getLanguageLabel(code) })),
  ]
}
