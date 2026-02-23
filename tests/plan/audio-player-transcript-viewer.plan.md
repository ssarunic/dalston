# Audio Player UI - Transcript Viewer Test Plan

## Application Overview

Test plan for the AudioPlayer and TranscriptViewer components embedded on batch job detail pages and realtime session detail pages. The TranscriptViewer provides a 3-zone sticky header (title+export, PII toggle, audio player controls) above a scrollable transcript. The AudioPlayer (row 3) wraps Plyr and adds skip-back/forward 10s buttons, an auto-scroll toggle, a download button, and a mobile overflow menu. Transcript rows are clickable to seek audio. Tests cover: layout structure, sticky header behavior, export dropdown, all audio player controls, tooltips, mobile responsive overflow menu, transcript click-to-seek, auto-scroll, keyboard shortcuts, error/loading states, playback position persistence, and PII toggle interaction with the audio source. Navigation entry point: /jobs (batch jobs list) then a completed job with audio.

## Test Scenarios

### 1. 3-Zone Header Layout

**Seed:** `tests/seed.spec.ts`

#### 1.1. Row 1 renders Transcript title and Export button

**File:** `tests/audio-player/layout.spec.ts`

**Steps:**

  1. Navigate to <http://localhost:5173/jobs> and log in if prompted
    - expect: The batch jobs list page is displayed
  2. Click on a completed job that has audio and a transcript
    - expect: The job detail page loads showing job metadata, pipeline, and a Transcript card
  3. Locate the Transcript card and inspect the first row of the sticky header
    - expect: The text 'Transcript' is visible as a heading in the top-left of the header row
    - expect: An 'Export' button with a download icon and chevron is visible in the top-right of the header row

#### 1.2. Row 2 is absent when PII is not enabled

**File:** `tests/audio-player/layout.spec.ts`

**Steps:**

  1. Navigate to a completed job that does NOT have PII detection enabled
    - expect: The job detail page loads
  2. Inspect the sticky header inside the Transcript card
    - expect: No PII toggle row (Original / Redacted buttons) is visible
    - expect: No PII badge is visible
    - expect: The header contains only the title row and the audio player row

#### 1.3. Row 2 renders PII toggle and badge when PII is enabled

**File:** `tests/audio-player/layout.spec.ts`

**Steps:**

  1. Navigate to a completed job that has PII detection enabled and shows entities detected > 0
    - expect: The job detail page loads
  2. Inspect the second row of the sticky header in the Transcript card
    - expect: A shield icon is visible on the left of the row
    - expect: Two adjacent buttons labelled 'Original' and 'Redacted' are visible
    - expect: The 'Original' button is active (highlighted/filled) by default
    - expect: A badge displaying the number of detected PII entities (e.g. '3 PII') is visible to the right of the toggle

#### 1.4. Row 3 renders the full-width audio player

**File:** `tests/audio-player/layout.spec.ts`

**Steps:**

  1. Navigate to a completed job that has audio available
    - expect: The job detail page loads
  2. Inspect the third row of the sticky header in the Transcript card on a desktop viewport (width >= 640px)
    - expect: A 'Skip back 10s' button (SkipBack icon) is visible on the left
    - expect: The Plyr audio player controls (play/pause, progress bar, current time, duration, mute, settings) span the centre
    - expect: A 'Skip forward 10s' button (SkipForward icon) is visible to the right of the player
    - expect: An auto-scroll toggle button (ListMusic icon) is visible
    - expect: A download audio button (Download icon) is visible at the far right

#### 1.5. Header is absent when no audio and no export and no PII

**File:** `tests/audio-player/layout.spec.ts`

**Steps:**

  1. Navigate to a job that is not completed and has no accessible audio (e.g. a pending or failed job without audio retention)
    - expect: The job detail page loads
  2. Inspect the Transcript card content area
    - expect: No sticky header is rendered
    - expect: An empty state message such as 'No transcript available' or 'Transcript not available for this job status' is shown

### 2. Sticky Header Scroll Behavior

**Seed:** `tests/seed.spec.ts`

#### 2.1. Header remains visible when scrolling through a long transcript

**File:** `tests/audio-player/sticky-header.spec.ts`

**Steps:**

  1. Navigate to a completed job with a long transcript (many segments) and audio
    - expect: The job detail page loads with the Transcript card visible
  2. Scroll the transcript content area downward so that multiple segments have passed out of view
    - expect: The transcript rows scroll as expected
  3. Without scrolling back up, check whether the header row (title, Export button, audio player controls) is still visible
    - expect: The sticky header remains fixed at the top of the Transcript card
    - expect: The 'Transcript' title is visible
    - expect: The Export button is visible
    - expect: The audio player controls (play/pause, skip buttons) are visible
    - expect: The transcript segment rows scroll underneath the header

#### 2.2. Header stays visible when the page itself is scrolled

**File:** `tests/audio-player/sticky-header.spec.ts`

**Steps:**

  1. Navigate to a completed job with audio and a transcript, on a page with content above the Transcript card (metadata, pipeline)
    - expect: The page loads normally
  2. Scroll the browser window downward until the Transcript card is in view and the title row of the header is at the top of the viewport
    - expect: The sticky header attaches to the top of the viewport (or the card boundary, depending on implementation)
  3. Continue scrolling through the transcript content
    - expect: The header with title, Export button, PII toggle (if applicable), and audio player controls remains in view
    - expect: Transcript rows scroll under the pinned header

### 3. Export Dropdown

**Seed:** `tests/seed.spec.ts`

#### 3.1. Export button opens dropdown with all four format options

**File:** `tests/audio-player/export.spec.ts`

**Steps:**

  1. Navigate to a completed job with a transcript and click the 'Export' button in the header row
    - expect: A dropdown menu appears below the Export button
  2. Inspect the dropdown menu items
    - expect: The menu contains exactly four options: 'SRT' (Subtitles), 'VTT' (Web Subtitles), 'TXT' (Plain Text), 'JSON' (Structured)
    - expect: Each option shows the format label in bold and a description in muted text beside it

#### 3.2. Clicking SRT triggers a file download

**File:** `tests/audio-player/export.spec.ts`

**Steps:**

  1. Open the Export dropdown on a completed job detail page
    - expect: The dropdown menu is visible
  2. Click the 'SRT' menu item
    - expect: The dropdown closes
    - expect: A file download is triggered (the browser initiates a download or a network request is made to the export endpoint)
    - expect: The Export button briefly shows a loading spinner while the download is in progress
    - expect: The Export button returns to its normal state after the download completes or fails

#### 3.3. Clicking VTT triggers a file download

**File:** `tests/audio-player/export.spec.ts`

**Steps:**

  1. Open the Export dropdown on a completed job detail page and click 'VTT'
    - expect: A download is initiated for the VTT format
    - expect: The Export button shows a spinner during the download

#### 3.4. Clicking TXT triggers a file download

**File:** `tests/audio-player/export.spec.ts`

**Steps:**

  1. Open the Export dropdown on a completed job detail page and click 'TXT'
    - expect: A download is initiated for the plain text format
    - expect: The Export button shows a spinner during the download

#### 3.5. Clicking JSON triggers a file download

**File:** `tests/audio-player/export.spec.ts`

**Steps:**

  1. Open the Export dropdown on a completed job detail page and click 'JSON'
    - expect: A download is initiated for the JSON format
    - expect: The Export button shows a spinner during the download

#### 3.6. Export button is disabled while a download is already in progress

**File:** `tests/audio-player/export.spec.ts`

**Steps:**

  1. Open the Export dropdown and click a format (e.g. SRT) to start a download
    - expect: The download starts and the Export button enters its loading state
  2. While the download spinner is visible, attempt to click the Export button again
    - expect: The Export button is disabled (not clickable) while a download is in progress
    - expect: The dropdown does not open a second time

#### 3.7. Export dropdown closes when clicking outside

**File:** `tests/audio-player/export.spec.ts`

**Steps:**

  1. Open the Export dropdown on a completed job
    - expect: The dropdown menu is visible
  2. Click anywhere on the page outside the dropdown menu
    - expect: The dropdown menu closes without triggering any download

### 4. Audio Player Controls

**Seed:** `tests/seed.spec.ts`

#### 4.1. Audio loads and player transitions from loading to ready state

**File:** `tests/audio-player/controls.spec.ts`

**Steps:**

  1. Navigate to a completed job with audio. Observe the audio player row immediately after the page loads
    - expect: A loading spinner and 'Loading...' text are visible while the audio source is being fetched and the Plyr player is initialising
  2. Wait for the audio to finish loading
    - expect: The loading spinner disappears
    - expect: The Plyr play button becomes active and interactive
    - expect: The progress bar and time display (0:00 / total duration) are visible
    - expect: The skip back and skip forward buttons become enabled

#### 4.2. Play and pause audio via the Plyr play button

**File:** `tests/audio-player/controls.spec.ts`

**Steps:**

  1. Navigate to a completed job with audio and wait for the player to be ready
    - expect: The Plyr player is in paused state
  2. Click the play button in the Plyr player
    - expect: Audio begins playing
    - expect: The play button changes to a pause icon
    - expect: The current time counter starts incrementing
  3. Click the pause button
    - expect: Audio pauses
    - expect: The pause button changes back to a play icon
    - expect: The current time counter stops

#### 4.3. Skip back 10 seconds button moves playback position backward

**File:** `tests/audio-player/controls.spec.ts`

**Steps:**

  1. Navigate to a completed job with audio, wait for the player to be ready, then start playback and let it run until the current time is at least 15 seconds
    - expect: The current time is at least 15 seconds
  2. Note the current playback time, then click the 'Skip back 10s' button (SkipBack icon, left of player)
    - expect: The current time decreases by approximately 10 seconds
    - expect: Playback continues from the new position without interruption

#### 4.4. Skip back 10s does not seek before 0:00

**File:** `tests/audio-player/controls.spec.ts`

**Steps:**

  1. Navigate to a completed job with audio, wait for the player to be ready, then seek to a position that is 5 seconds into the audio
    - expect: Current time shows approximately 0:05
  2. Click the 'Skip back 10s' button
    - expect: The current time is clamped to 0:00 and does not go negative
    - expect: Playback resumes from 0:00

#### 4.5. Skip forward 10 seconds button moves playback position forward

**File:** `tests/audio-player/controls.spec.ts`

**Steps:**

  1. Navigate to a completed job with audio, wait for the player to be ready (not playing). Note the current time (0:00)
    - expect: Player is ready at 0:00
  2. Click the 'Skip forward 10s' button (SkipForward icon, right of player)
    - expect: The current time advances by approximately 10 seconds
    - expect: The progress bar updates to reflect the new position

#### 4.6. Skip forward 10s does not exceed the total duration

**File:** `tests/audio-player/controls.spec.ts`

**Steps:**

  1. Navigate to a completed job with audio, wait for the player to be ready, then seek to a position 5 seconds before the end of the audio
    - expect: Current time is near the end of the audio
  2. Click the 'Skip forward 10s' button
    - expect: The current time is clamped to the total duration and does not exceed it
    - expect: The player does not error

#### 4.7. Skip buttons are disabled before the player is ready

**File:** `tests/audio-player/controls.spec.ts`

**Steps:**

  1. Navigate to a completed job with audio and immediately inspect the skip back and skip forward buttons before loading completes
    - expect: Both skip buttons are rendered in a disabled state
    - expect: Clicking a disabled skip button produces no change in playback time

#### 4.8. Volume control mutes and unmutes audio

**File:** `tests/audio-player/controls.spec.ts`

**Steps:**

  1. Navigate to a completed job with audio, wait for the player to be ready and start playback
    - expect: Audio is audible / volume icon shows unmuted state
  2. Click the mute button in the Plyr controls
    - expect: The volume icon changes to the muted state
  3. Click the mute button again
    - expect: The volume icon returns to the unmuted state

#### 4.9. Playback speed can be changed via the settings menu

**File:** `tests/audio-player/controls.spec.ts`

**Steps:**

  1. Navigate to a completed job with audio, wait for the player to be ready, then click the settings (gear) button in the Plyr controls
    - expect: A settings panel appears showing Speed options
  2. Select 1.5x playback speed
    - expect: The settings panel closes or updates
    - expect: Playback speed is set to 1.5x (verifiable by playing a known-duration audio clip and timing it)
  3. Open settings again and verify the available speed options
    - expect: The available speeds are: 0.5, 0.75, 1, 1.25, 1.5, 2

#### 4.10. Progress bar click seeks to the clicked position

**File:** `tests/audio-player/controls.spec.ts`

**Steps:**

  1. Navigate to a completed job with audio, wait for the player to be ready
    - expect: Player is at 0:00
  2. Click approximately in the middle of the Plyr progress bar
    - expect: The current time jumps to approximately the middle of the total duration
    - expect: The displayed current time updates accordingly

#### 4.11. Download audio button triggers download

**File:** `tests/audio-player/controls.spec.ts`

**Steps:**

  1. Navigate to a completed job with audio, wait for the player to be ready, then click the Download audio button (Download icon, far right on desktop)
    - expect: The download button briefly shows a loading spinner (Loader2) while the download URL is resolved
    - expect: A file download is triggered in the browser
    - expect: The button returns to its normal state after the download completes

#### 4.12. Download button is disabled when no audio source and no download resolver

**File:** `tests/audio-player/controls.spec.ts`

**Steps:**

  1. Render the audio player in a state where no audio source URL is available and no onResolveDownloadUrl callback is provided (e.g. a job where audio is unavailable)
    - expect: The Download audio button is rendered in a disabled state
    - expect: Clicking the button produces no action

### 5. Auto-Scroll Toggle

**Seed:** `tests/seed.spec.ts`

#### 5.1. Auto-scroll is disabled by default

**File:** `tests/audio-player/autoscroll.spec.ts`

**Steps:**

  1. Navigate to a completed job with audio and transcript, wait for the player to be ready
    - expect: The auto-scroll toggle button (ListMusic icon) is visible
    - expect: The button is in its default ghost/inactive state (not highlighted with secondary variant)
    - expect: The aria-pressed attribute of the button is false

#### 5.2. Clicking auto-scroll toggle enables auto-scroll

**File:** `tests/audio-player/autoscroll.spec.ts`

**Steps:**

  1. Navigate to a completed job with audio and transcript, wait for the player to be ready, then click the auto-scroll toggle button (ListMusic icon)
    - expect: The button visual state changes to the 'secondary' variant (highlighted/filled)
    - expect: The aria-pressed attribute of the button becomes true
    - expect: The tooltip text changes to 'Auto-scroll enabled'

#### 5.3. Auto-scroll scrolls transcript to follow active segment during playback

**File:** `tests/audio-player/autoscroll.spec.ts`

**Steps:**

  1. Navigate to a completed job with a long transcript and audio, enable auto-scroll by clicking the toggle button
    - expect: Auto-scroll is enabled
  2. Start playback and wait until a transcript segment beyond the initial viewport is reached
    - expect: The transcript scroll container automatically scrolls so that the currently-playing segment is centred in the visible area
    - expect: The active segment row is highlighted (primary background tint, left border accent, primary-coloured timestamp)

#### 5.4. Clicking auto-scroll toggle again disables auto-scroll

**File:** `tests/audio-player/autoscroll.spec.ts`

**Steps:**

  1. Enable auto-scroll, confirm it is active, then click the toggle button a second time
    - expect: The button returns to ghost/inactive visual state
    - expect: The aria-pressed attribute becomes false
    - expect: The tooltip text changes back to 'Enable auto-scroll'
    - expect: Playback continues without auto-scrolling the transcript

### 6. Tooltip Behavior

**Seed:** `tests/seed.spec.ts`

#### 6.1. Skip back button shows tooltip on hover

**File:** `tests/audio-player/tooltips.spec.ts`

**Steps:**

  1. Navigate to a completed job with audio on a desktop viewport (>= 640px), wait for the player to be ready
    - expect: The skip back button is visible
  2. Hover the mouse cursor over the skip back button (SkipBack icon) for at least 200ms
    - expect: A tooltip appears below the button
    - expect: The tooltip text reads 'Back 10s'
  3. Move the mouse away from the skip back button
    - expect: The tooltip disappears

#### 6.2. Skip forward button shows tooltip on hover

**File:** `tests/audio-player/tooltips.spec.ts`

**Steps:**

  1. Hover over the skip forward button (SkipForward icon) for at least 200ms
    - expect: A tooltip appears below the button
    - expect: The tooltip text reads 'Forward 10s'

#### 6.3. Auto-scroll button shows tooltip on hover when disabled

**File:** `tests/audio-player/tooltips.spec.ts`

**Steps:**

  1. Hover over the auto-scroll toggle button (ListMusic icon) when auto-scroll is off for at least 200ms
    - expect: A tooltip appears
    - expect: The tooltip text reads 'Enable auto-scroll'

#### 6.4. Auto-scroll button shows updated tooltip on hover when enabled

**File:** `tests/audio-player/tooltips.spec.ts`

**Steps:**

  1. Enable auto-scroll by clicking the toggle button, then hover over it for at least 200ms
    - expect: A tooltip appears
    - expect: The tooltip text reads 'Auto-scroll enabled'

#### 6.5. Download audio button shows tooltip on hover

**File:** `tests/audio-player/tooltips.spec.ts`

**Steps:**

  1. Hover over the download audio button (Download icon) for at least 200ms
    - expect: A tooltip appears below the button
    - expect: The tooltip text reads 'Download audio'

#### 6.6. Tooltip appears on keyboard focus

**File:** `tests/audio-player/tooltips.spec.ts`

**Steps:**

  1. Use the keyboard Tab key to focus the skip back button
    - expect: The tooltip appears immediately (no delay on focus)
    - expect: The tooltip text reads 'Back 10s'
  2. Press Tab to move focus away from the skip back button
    - expect: The tooltip disappears

### 7. Mobile Responsive Overflow Menu

**Seed:** `tests/seed.spec.ts`

#### 7.1. On narrow viewport skip buttons and secondary controls are hidden

**File:** `tests/audio-player/mobile.spec.ts`

**Steps:**

  1. Navigate to a completed job with audio and resize the browser viewport to 390px wide (below the 640px breakpoint)
    - expect: The page reflows to mobile layout
  2. Inspect the audio player row in the Transcript card header
    - expect: The skip back button (SkipBack icon) is NOT visible as a standalone button
    - expect: The skip forward button (SkipForward icon) is NOT visible as a standalone button
    - expect: The auto-scroll toggle button (ListMusic icon) is NOT visible as a standalone button
    - expect: The download audio button (Download icon) is NOT visible as a standalone button
    - expect: A 'More options' button (MoreVertical icon, three vertical dots) IS visible at the right side of the audio player row

#### 7.2. More options menu opens and lists all collapsed controls

**File:** `tests/audio-player/mobile.spec.ts`

**Steps:**

  1. On a viewport narrower than 640px, navigate to a completed job with audio, wait for the player to be ready, then click the 'More options' button (MoreVertical icon)
    - expect: A dropdown menu appears
    - expect: The menu contains four items: 'Back 10s' (SkipBack icon), 'Forward 10s' (SkipForward icon), 'Enable auto-scroll' (ListMusic icon), 'Download audio' (Download icon)

#### 7.3. Back 10s in overflow menu seeks backward

**File:** `tests/audio-player/mobile.spec.ts`

**Steps:**

  1. On mobile viewport, start playback and let audio reach at least 15 seconds, open the More options menu, note the current time, then click 'Back 10s'
    - expect: The menu closes
    - expect: The current time decreases by approximately 10 seconds

#### 7.4. Forward 10s in overflow menu seeks forward

**File:** `tests/audio-player/mobile.spec.ts`

**Steps:**

  1. On mobile viewport, wait for the player to be ready at 0:00, open the More options menu, then click 'Forward 10s'
    - expect: The menu closes
    - expect: The current time advances by approximately 10 seconds

#### 7.5. Back 10s and Forward 10s are disabled in overflow menu when player is not ready

**File:** `tests/audio-player/mobile.spec.ts`

**Steps:**

  1. On mobile viewport, open the More options menu before the audio has loaded (during the loading spinner phase)
    - expect: The 'Back 10s' menu item is disabled (greyed out, not interactive)
    - expect: The 'Forward 10s' menu item is disabled (greyed out, not interactive)

#### 7.6. Auto-scroll in overflow menu toggles auto-scroll

**File:** `tests/audio-player/mobile.spec.ts`

**Steps:**

  1. On mobile viewport, open the More options menu and click 'Enable auto-scroll'
    - expect: The menu closes
    - expect: Auto-scroll is now enabled (confirmed by re-opening the menu and seeing 'Disable auto-scroll')
  2. Open the More options menu again and click 'Disable auto-scroll'
    - expect: The menu closes
    - expect: Auto-scroll is now disabled (confirmed by re-opening the menu and seeing 'Enable auto-scroll')

#### 7.7. Download audio in overflow menu triggers download

**File:** `tests/audio-player/mobile.spec.ts`

**Steps:**

  1. On mobile viewport, open the More options menu and click 'Download audio'
    - expect: The menu closes
    - expect: A file download is initiated

#### 7.8. Plyr player itself is still visible and functional on mobile

**File:** `tests/audio-player/mobile.spec.ts`

**Steps:**

  1. On mobile viewport, inspect the Plyr player area in the audio player row
    - expect: The Plyr player (play/pause button, progress bar, time display) is still visible and fills the available width
    - expect: Clicking play starts audio playback

#### 7.9. Switching from mobile to desktop viewport restores inline controls

**File:** `tests/audio-player/mobile.spec.ts`

**Steps:**

  1. Start on a viewport < 640px wide with the audio player visible, then resize the viewport to >= 640px
    - expect: The More options (MoreVertical) button disappears
    - expect: The skip back, skip forward, auto-scroll toggle, and download buttons appear as standalone inline buttons
    - expect: No overflow menu is needed

### 8. Transcript Click-to-Seek

**Seed:** `tests/seed.spec.ts`

#### 8.1. Clicking a transcript row seeks audio to that segment's start time

**File:** `tests/audio-player/seek.spec.ts`

**Steps:**

  1. Navigate to a completed job with audio and transcript segments, wait for the player to be ready
    - expect: Player is at 0:00 in paused state
  2. Find a transcript row that is not at 0:00 (e.g. a row at 0:45), note its timestamp, and click the row
    - expect: The audio player's current time jumps to the timestamp shown in the clicked row (approximately 0:45)
    - expect: Playback starts automatically from that position
    - expect: The clicked row is highlighted as the active segment (primary background tint and left border accent)

#### 8.2. Clicking different transcript rows seeks to each respective timestamp

**File:** `tests/audio-player/seek.spec.ts`

**Steps:**

  1. Navigate to a completed job with audio and at least three transcript segments. Click the second segment row
    - expect: Audio seeks to the second segment's start time and playback begins
  2. Click the first segment row
    - expect: Audio seeks back to the first segment's start time
    - expect: The first row becomes the active (highlighted) row
    - expect: The second row is no longer highlighted

#### 8.3. Transcript rows are visually clickable when audio is available

**File:** `tests/audio-player/seek.spec.ts`

**Steps:**

  1. Navigate to a completed job with audio and inspect the transcript rows
    - expect: Each transcript row shows a pointer cursor on hover
    - expect: Hovering a row shows a hover background highlight (bg-muted/50)
    - expect: The timestamp column changes to primary colour on hover

#### 8.4. Transcript rows are not clickable when there is no audio source

**File:** `tests/audio-player/seek.spec.ts`

**Steps:**

  1. Navigate to a completed job that has no audio available (purged audio or unsupported status), inspect the transcript rows
    - expect: Transcript rows do not have a pointer cursor
    - expect: Clicking a row does not change the audio player position (no player is active)
    - expect: No seek action is triggered

#### 8.5. Active segment highlight follows playback position in real time

**File:** `tests/audio-player/seek.spec.ts`

**Steps:**

  1. Navigate to a completed job with audio and transcript, start playback
    - expect: The player starts playing
  2. Watch the transcript area as playback progresses through multiple segment boundaries
    - expect: As the audio reaches a new segment, that segment's row becomes highlighted (primary/10 background, left primary border, primary-coloured timestamp)
    - expect: The previously active row loses its highlight
    - expect: Only one row is active at a time

#### 8.6. Seeking before first segment start shows no active segment

**File:** `tests/audio-player/seek.spec.ts`

**Steps:**

  1. Navigate to a completed job where the first segment does not start at 0:00 (there is a gap at the beginning). Seek to a time before the first segment start
    - expect: No transcript row is highlighted
    - expect: The activeSegmentIndex is -1

#### 8.7. Seeking into a gap between segments shows no active segment

**File:** `tests/audio-player/seek.spec.ts`

**Steps:**

  1. On a job where two segments have a gap between them (segment 1 ends at t=10, segment 2 starts at t=12), seek to t=11
    - expect: No segment row is highlighted during the gap
    - expect: When playback reaches t=12 the second segment becomes highlighted

### 9. Keyboard Shortcuts

**Seed:** `tests/seed.spec.ts`

#### 9.1. Space bar toggles play/pause

**File:** `tests/audio-player/keyboard.spec.ts`

**Steps:**

  1. Navigate to a completed job with audio, wait for player to be ready, ensure focus is not inside a text input, then press the Space key
    - expect: Audio starts playing (play button becomes pause icon)
  2. Press Space again
    - expect: Audio pauses (pause button reverts to play icon)

#### 9.2. Arrow Left seeks back 5 seconds

**File:** `tests/audio-player/keyboard.spec.ts`

**Steps:**

  1. Start playback and let the audio reach at least 10 seconds, note the current time, ensure focus is not on an input, then press the Left Arrow key
    - expect: The current time decreases by approximately 5 seconds
    - expect: The progress bar updates to reflect the new position

#### 9.3. Arrow Right seeks forward 5 seconds

**File:** `tests/audio-player/keyboard.spec.ts`

**Steps:**

  1. With the player ready at 0:00, ensure focus is not on an input, then press the Right Arrow key
    - expect: The current time advances by approximately 5 seconds
    - expect: The progress bar updates

#### 9.4. Arrow Left clamps at 0 and does not go negative

**File:** `tests/audio-player/keyboard.spec.ts`

**Steps:**

  1. With the player at 2 seconds, press the Left Arrow key
    - expect: Current time becomes 0:00 and does not go negative

#### 9.5. Arrow Right clamps at total duration

**File:** `tests/audio-player/keyboard.spec.ts`

**Steps:**

  1. Seek to 3 seconds before the end of the audio, then press the Right Arrow key
    - expect: Current time is clamped to the total duration

#### 9.6. J key navigates to the next transcript segment

**File:** `tests/audio-player/keyboard.spec.ts`

**Steps:**

  1. Navigate to a completed job with multiple transcript segments and audio. Click the first segment to set it as active, then press the J key (without any modifier keys)
    - expect: Audio seeks to the start of the second transcript segment
    - expect: The second segment row becomes active/highlighted

#### 9.7. K key navigates to the previous transcript segment

**File:** `tests/audio-player/keyboard.spec.ts`

**Steps:**

  1. Navigate to a completed job with multiple transcript segments and audio. Click the second segment to set it as active, then press the K key (without modifier keys)
    - expect: Audio seeks to the start of the first transcript segment
    - expect: The first segment row becomes active/highlighted

#### 9.8. Keyboard shortcuts do not fire when focus is on an input field

**File:** `tests/audio-player/keyboard.spec.ts`

**Steps:**

  1. Navigate to a page that has a text input field alongside the audio player. Click into a text input to focus it, then press Space
    - expect: The space character is typed into the input field
    - expect: Audio playback is NOT toggled

#### 9.9. J and K keys with modifier keys do not navigate segments

**File:** `tests/audio-player/keyboard.spec.ts`

**Steps:**

  1. Navigate to a completed job with audio, ensure focus is on the body. Press Ctrl+J (or Cmd+J)
    - expect: No segment navigation occurs (the browser or OS default shortcut may fire, but the audio player does not respond)

### 10. Audio Error and Loading States

**Seed:** `tests/seed.spec.ts`

#### 10.1. Loading state shows spinner while audio source is fetching

**File:** `tests/audio-player/error-states.spec.ts`

**Steps:**

  1. Navigate to a completed job with audio and immediately observe the audio player row before the source URL resolves
    - expect: A spinning Loader2 icon and 'Loading...' text are displayed inside the player area
    - expect: Skip buttons are disabled

#### 10.2. Error state shows alert icon and Retry button when audio fails to load

**File:** `tests/audio-player/error-states.spec.ts`

**Steps:**

  1. Simulate an audio load failure (e.g. by having the server return an error for the audio URL, or by blocking the audio URL at the network level). Navigate to the job detail page
    - expect: An AlertCircle icon is visible
    - expect: The text 'Failed' is displayed in red
    - expect: A Retry button (RefreshCw icon) is visible

#### 10.3. Retry button refreshes the audio source URLs and attempts to reload

**File:** `tests/audio-player/error-states.spec.ts`

**Steps:**

  1. With audio in a failed/error state, click the Retry button
    - expect: The Retry button shows a spinning Loader2 icon while refreshing URLs
    - expect: The onRefreshSourceUrls callback is invoked (a new presigned URL is fetched from the API)
    - expect: After the refresh completes, the player attempts to load audio again
    - expect: If the new URL is valid, the error state clears and the player becomes ready

#### 10.4. Unavailable audio state shows Audio unavailable message with Retry

**File:** `tests/audio-player/error-states.spec.ts`

**Steps:**

  1. Render the audio player when no audio source URL is available at all (activeSrc is null/undefined)
    - expect: The Plyr audio element is NOT rendered
    - expect: The text 'Audio unavailable' is displayed
    - expect: A Retry button (RefreshCw icon) is visible if onRefreshSourceUrls is provided

#### 10.5. Retry button is disabled when no onRefreshSourceUrls callback is provided

**File:** `tests/audio-player/error-states.spec.ts`

**Steps:**

  1. Render the audio player in the 'Audio unavailable' state without providing an onRefreshSourceUrls prop
    - expect: The Retry button is rendered in a disabled state and clicking it produces no action

### 11. Playback Position Persistence

**Seed:** `tests/seed.spec.ts`

#### 11.1. Playback position is saved to sessionStorage during playback

**File:** `tests/audio-player/persistence.spec.ts`

**Steps:**

  1. Navigate to a completed job with audio, wait for the player to be ready, start playback, and let it run for at least 5 seconds
    - expect: Audio is playing at a position > 0 seconds
  2. Wait up to 3 seconds (the save interval is 2s), then inspect sessionStorage for a key matching `dalston:playback:<audio-path>`
    - expect: A sessionStorage entry exists with a key starting with 'dalston:playback:'
    - expect: The stored value is the current playback time as a numeric string (e.g. '7.2')

#### 11.2. Returning to the same job restores the saved playback position

**File:** `tests/audio-player/persistence.spec.ts`

**Steps:**

  1. Navigate to a completed job, play audio to approximately 30 seconds, then navigate away to the jobs list page
    - expect: The jobs list page loads and the job detail page is no longer shown
  2. Navigate back to the same job detail page
    - expect: The audio player restores the saved position (~30 seconds) rather than starting at 0:00
    - expect: The current time display shows the restored position

#### 11.3. Switching between original and redacted audio resets position restoration for the new source

**File:** `tests/audio-player/persistence.spec.ts`

**Steps:**

  1. Navigate to a completed job that has PII redaction with both original and redacted audio. Play the original audio to about 20 seconds
    - expect: Position is approximately 20 seconds on original audio
  2. Click the 'Redacted' button in the PII toggle to switch to redacted audio
    - expect: The audio source switches to the redacted URL
    - expect: The player attempts to restore any previously saved position for the redacted audio URL
    - expect: If no position was previously saved for the redacted URL, playback starts at 0:00

#### 11.4. Position is saved to sessionStorage when leaving the page

**File:** `tests/audio-player/persistence.spec.ts`

**Steps:**

  1. Navigate to a completed job, play audio to 15 seconds, then navigate to a different page (e.g. /jobs) without waiting for the 2s auto-save interval
    - expect: On unmount, the Plyr cleanup function runs and saves the current time to sessionStorage
    - expect: Navigating back to the job detail page restores the position to approximately 15 seconds

### 12. PII Toggle Integration with Audio Player

**Seed:** `tests/seed.spec.ts`

#### 12.1. Selecting Redacted switches transcript text to redacted version

**File:** `tests/audio-player/pii.spec.ts`

**Steps:**

  1. Navigate to a completed job with PII enabled and per-segment redacted text. Observe the transcript content in 'Original' mode
    - expect: The transcript rows show the original (unredacted) text
  2. Click the 'Redacted' button in the PII toggle row
    - expect: The 'Redacted' button becomes active (highlighted)
    - expect: The 'Original' button becomes inactive
    - expect: The transcript rows now display the redacted text (e.g. with PII placeholders) instead of the original text

#### 12.2. Selecting Original switches transcript text back to original version

**File:** `tests/audio-player/pii.spec.ts`

**Steps:**

  1. With the 'Redacted' view active, click the 'Original' button
    - expect: The 'Original' button becomes active
    - expect: The transcript rows display the original unredacted text again

#### 12.3. Selecting Redacted switches audio to the redacted audio source

**File:** `tests/audio-player/pii.spec.ts`

**Steps:**

  1. Navigate to a completed job with PII enabled and a redacted audio file available. Start playback on the original audio, then click the 'Redacted' button
    - expect: The audio element's src attribute changes to the redacted audio URL
    - expect: The player loads the new source (loading state briefly appears if needed)
    - expect: Playback continues from approximately the same position on the redacted audio track

#### 12.4. Selecting Original after Redacted switches audio back to original source

**File:** `tests/audio-player/pii.spec.ts`

**Steps:**

  1. With redacted audio playing, click the 'Original' button
    - expect: The audio element's src changes back to the original audio URL
    - expect: The player loads the original audio

#### 12.5. PII badge shows correct entity count

**File:** `tests/audio-player/pii.spec.ts`

**Steps:**

  1. Navigate to a completed job where PII detection found entities (e.g. 5 PII entities detected)
    - expect: The PII badge displays the correct number: '5 PII'
    - expect: The badge uses the secondary variant styling

#### 12.6. PII badge is not shown when entitiesDetected is 0 or undefined

**File:** `tests/audio-player/pii.spec.ts`

**Steps:**

  1. Navigate to a completed job where PII is enabled but entitiesDetected is 0 or not set
    - expect: The PII toggle row is visible (if there is redacted text at the segment or full-text level)
    - expect: No PII badge is rendered in the toggle row

### 13. Virtualized Transcript Rendering

**Seed:** `tests/seed.spec.ts`

#### 13.1. Transcripts with fewer than 100 segments render as a standard list

**File:** `tests/audio-player/virtualization.spec.ts`

**Steps:**

  1. Navigate to a completed job with fewer than 100 transcript segments
    - expect: All segment rows are rendered in the DOM as standard div elements
    - expect: No virtualization container with absolute positioning is used
    - expect: Scrolling through the transcript renders all items

#### 13.2. Transcripts with 100 or more segments use virtualised rendering

**File:** `tests/audio-player/virtualization.spec.ts`

**Steps:**

  1. Navigate to a completed job with 100 or more transcript segments
    - expect: A single container div with a fixed height equal to the total estimated size is present
    - expect: Only a subset of segment rows are in the DOM at any time (the visible window + 10 overscan rows)
    - expect: Scrolling through the transcript loads new rows into the DOM dynamically

#### 13.3. Auto-scroll works correctly with virtualized transcript

**File:** `tests/audio-player/virtualization.spec.ts`

**Steps:**

  1. Navigate to a completed job with 100+ segments and audio. Enable auto-scroll, then start playback and let it reach a segment far into the transcript (e.g. segment 50)
    - expect: The virtualizer scrolls to index 50 with smooth behavior
    - expect: The active segment row is centred in the visible area
    - expect: The active segment is highlighted correctly

### 14. Realtime Session Detail - TranscriptViewer

**Seed:** `tests/seed.spec.ts`

#### 14.1. Transcript viewer on a realtime session detail page renders correctly

**File:** `tests/audio-player/realtime.spec.ts`

**Steps:**

  1. Navigate to /realtime and click on a session that has stored audio and a transcript
    - expect: The realtime session detail page loads
  2. Locate the Transcript card on the session detail page
    - expect: The TranscriptViewer is rendered with the session's transcript segments
    - expect: The audio player row is visible with the stored session audio
    - expect: The Export button is present (for sessions with a transcript)

#### 14.2. Export on realtime session calls the session export endpoint

**File:** `tests/audio-player/realtime.spec.ts`

**Steps:**

  1. On a realtime session detail page, click the Export button and select SRT
    - expect: The API call is made to the session export endpoint (not the job export endpoint)
    - expect: A file download is triggered
