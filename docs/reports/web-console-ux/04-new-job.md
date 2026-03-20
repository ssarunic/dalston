# 04 — Submit New Job

**Route:** `/jobs/new`
**Component:** `src/pages/NewJob.tsx`
**Auth required:** Yes

## Purpose

Multi-section form to create a new batch transcription job. Supports file upload or URL source, model selection, language, speaker detection, PII redaction, and advanced settings.

## Storyboard

```
┌──────────────────────────────────────────────────────────────────┐
│                                                                  │
│  ← Back to Jobs                                                  │
│  Submit Batch Job                                                │
│  Upload audio or provide an audio URL to create a transcription  │
│                                                                  │
│  ┌─────────────────────────────────────┐  ┌────────────────────┐ │
│  │                                     │  │ Summary            │ │
│  │ Audio Source                         │  │                    │ │
│  │ ┌──────────────┬──────────────┐     │  │ Source: meeting.mp3│ │
│  │ │ ▣ Upload File│  🔗 Audio URL│     │  │ Language: Auto     │ │
│  │ └──────────────┴──────────────┘     │  │ Speakers: Diarize  │ │
│  │                                     │  │ Timestamps: Word   │ │
│  │ ┌─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┐  │  │ PII: Enabled       │ │
│  │ │  Drop your audio file here    │  │  └────────────────────┘ │
│  │ │  or click to browse           │  │                          │
│  │ │  MP3, WAV, FLAC, OGG, M4A    │  │  ┌────────────────────┐ │
│  │ └─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┘  │  │ 💡 Tips            │ │
│  │                                     │  │                    │ │
│  └─────────────────────────────────────┘  │ For best results,  │ │
│                                           │ use high-quality   │ │
│  ┌─────────────────────────────────────┐  │ audio...           │ │
│  │ Basic Settings                      │  └────────────────────┘ │
│  │                                     │                          │
│  │ Model             Language          │                          │
│  │ ┌─────────────┐  ┌──────────────┐  │                          │
│  │ │ Auto       ▾│  │ Auto-detect ▾│  │                          │
│  │ └─────────────┘  └──────────────┘  │                          │
│  │                                     │                          │
│  │ Speaker Detection  Timestamps       │                          │
│  │ ┌─────────────┐  ┌──────────────┐  │                          │
│  │ │ Diarize    ▾│  │ Word        ▾│  │                          │
│  │ └─────────────┘  └──────────────┘  │                          │
│  │                                     │                          │
│  │ Diarizer      Speakers  Min  Max    │                          │
│  │ ┌──────────┐  ┌─────┐ ┌───┐ ┌───┐  │                          │
│  │ │ Auto    ▾│  │     │ │   │ │   │  │                          │
│  │ └──────────┘  └─────┘ └───┘ └───┘  │                          │
│  └─────────────────────────────────────┘                          │
│                                                                  │
│  ┌─────────────────────────────────────┐                          │
│  │ Advanced Settings              [▾]  │                          │
│  │ Leave fields at defaults unless...  │                          │
│  │─────────────────────────────────────│                          │
│  │ Retention Policy    Vocabulary      │  (expanded)              │
│  │ ┌──────────────┐                   │                          │
│  │ │ Server def. ▾│  ┌────────────┐   │                          │
│  │ └──────────────┘  │ terms,...  │   │                          │
│  │                   └────────────┘   │                          │
│  │─────────────────────────────────────│                          │
│  │ PII Detection               [🔘]   │                          │
│  │                                     │                          │
│  │ Entity Types: [Default ▾]           │                          │
│  │ 12 types selected   [Customize]     │                          │
│  │ [Name][Email][Phone][SSN][Location] │                          │
│  │ [DOB][IP][Credit Card][CVV]...      │                          │
│  │                                     │                          │
│  │ Redact PII in Audio         [🔘]   │                          │
│  │ Redaction Mode: [Silence ▾]         │                          │
│  └─────────────────────────────────────┘                          │
│                                                                  │
│                       [Cancel]  [Submit Job]                     │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Mobile: Sticky bottom action bar

```
┌────────────────────────────────┐
│ (scrollable form content...)   │
│                                │
├────────────────────────────────┤
│  [  Cancel  ]  [ Submit Job ]  │  ← fixed bottom bar
└────────────────────────────────┘
```

## Form Sections

### Audio Source
- **Segmented control:** "Upload File" / "Audio URL" toggle
- **File upload:** Drag-and-drop zone with dashed border. Shows file name, size, and remove button when file selected.
- **URL input:** Text field with URL validation.
- **Validation:** Required, URL format check.

### Basic Settings (2×2 grid)
| Field | Type | Options | Notes |
|-------|------|---------|-------|
| Model | `<ModelSelector>` custom component | Auto + all ready models from registry | Shows compatibility warning if model doesn't support selected language |
| Language | Select | Auto-detect + languages (filtered by model capabilities) | 90+ languages supported |
| Speaker Detection | Select | None / Diarize / Per channel | Conditionally shows diarizer options |
| Timestamps | Select | None / Segment / Word | |

### Diarization Options (conditional, shown when speaker detection ≠ none)
| Field | Type | Validation |
|-------|------|------------|
| Diarizer | Select | Auto + available diarizer engines (from capabilities API) |
| Speakers | Number input | 1-32, optional |
| Min Speakers | Number input | 1-32, optional, must ≤ max |
| Max Speakers | Number input | 1-32, optional, must ≥ min |

### Advanced Settings (collapsible card)
- **Click header to expand/collapse** with chevron animation.
- When collapsed: shows hint text "Leave fields at defaults unless..."

#### Retention Policy
- Select: Server default / Don't store / Keep forever / Delete after...
- "Delete after" shows numeric input + "days" label.

#### Vocabulary
- Textarea for comma-separated terms (max 100).

#### PII Detection
- **Toggle switch** to enable/disable.
- **Entity Types preset:** Default / PCI Compliance / HIPAA / Personal Only / Custom.
- **Type count display** with "Customize" link.
- **Chip display** of selected entity types with individual remove buttons.
- **Customization panel:** Categorized checkboxes (Personal, Payment/PCI, Health/HIPAA) in a bordered panel.
- **Redact PII in Audio:** Second toggle, only shown when PII detection enabled.
- **Redaction Mode:** Silence / Beep, only shown when audio redaction enabled.

### Right Sidebar (desktop only, `lg:block`)
- **Summary card:** Live preview of selected options.
- **Tips card:** Static guidance text.

## Behaviour

1. **Form validation** runs on submit: source required, speaker count ranges, vocabulary limit.
2. **Field-level errors** shown inline below each field.
3. **Submission:** `useCreateJob` mutation sends `multipart/form-data` (file) or JSON (URL). Shows "Submitting..." on button.
4. **Success:** Navigate to `/jobs/:newJobId`.
5. **Failure:** Red error banner at bottom of form with error message from backend.
6. **Cancel:** Navigate back to `/jobs`.

## States

| State | Visual |
|-------|--------|
| Fresh form | Empty dropzone, all defaults |
| File selected | File info bar with name, size, remove button |
| Drag over | Dropzone border turns primary color |
| Validation errors | Red text below offending fields |
| Submitting | Button disabled with "Submitting..." text |
| Submission error | Red banner with AlertCircle icon |
