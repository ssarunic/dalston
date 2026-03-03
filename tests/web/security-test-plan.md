# Dalston Web Console Security Test Plan

## Application Overview

The Dalston web console is a React management interface for a self-hosted audio transcription server. It is served at <http://localhost:3007> (proxied to the FastAPI gateway on port 8000). The console requires authentication via an API key with admin scope. Session state is stored in sessionStorage (not localStorage), so sessions do not persist across browser tabs or browser restarts. All API calls use an Authorization Bearer header. The console provides management of: batch transcription jobs, real-time sessions, engines, models, API keys, webhooks, an audit log, and system settings. Key security behaviors identified: (1) All routes except /login are protected by a ProtectedRoute component that redirects unauthenticated users to /login. (2) The login flow calls GET /auth/me with the provided key and requires the returned scopes array to contain "admin". Valid non-admin keys are explicitly rejected with an error message. (3) API key values are masked in the UI (showing only the first 10 characters) after creation; full key values are shown only once at creation time. (4) Admin scope is mutually exclusive with other scopes in the key creation form. (5) Webhook signing secrets follow the same one-time display pattern as API keys. (6) The audit log tracks actor identity (key prefix) and source IP address for all job events.

## Test Scenarios

### 1. Authentication Flow

**Seed:** ``

#### 1.1. Unauthenticated access redirects to login page

**File:** `tests/web/auth/unauthenticated-redirect.spec.ts`

**Steps:**

  1. Open a fresh browser session (no stored API key). Navigate directly to <http://localhost:3007/>
    - expect: The browser URL changes to <http://localhost:3007/login>
    - expect: The login page is displayed with a heading 'Dalston Console'
    - expect: No dashboard or console content is visible
  2. Navigate directly to <http://localhost:3007/keys>
    - expect: The browser URL changes to <http://localhost:3007/login>
    - expect: The login page is displayed
  3. Navigate directly to <http://localhost:3007/audit>
    - expect: The browser URL changes to <http://localhost:3007/login>
    - expect: The login page is displayed
  4. Navigate directly to <http://localhost:3007/settings>
    - expect: The browser URL changes to <http://localhost:3007/login>
    - expect: The login page is displayed
  5. Navigate directly to <http://localhost:3007/webhooks>
    - expect: The browser URL changes to <http://localhost:3007/login>
    - expect: The login page is displayed
  6. Navigate directly to <http://localhost:3007/jobs>
    - expect: The browser URL changes to <http://localhost:3007/login>
    - expect: The login page is displayed

#### 1.2. Login page is accessible without authentication

**File:** `tests/web/auth/login-page-access.spec.ts`

**Steps:**

  1. Open a fresh browser session. Navigate to <http://localhost:3007/login>
    - expect: The page loads without redirecting
    - expect: A heading 'Dalston Console' is visible
    - expect: A password-type input field with placeholder 'dk_...' is present
    - expect: A 'Login' button is visible and disabled when the field is empty
    - expect: A helper text reading 'Create an admin key with: python -m dalston.gateway.cli create-key --scopes admin' is visible

#### 1.3. Login button is disabled for empty and whitespace-only input

**File:** `tests/web/auth/login-button-state.spec.ts`

**Steps:**

  1. Navigate to <http://localhost:3007/login>. Observe the Login button state without entering any text.
    - expect: The 'Login' button is disabled
  2. Click into the API Key input field and type three space characters.
    - expect: The 'Login' button remains disabled (whitespace-only input is treated as empty)
  3. Clear the field and type a single non-whitespace character.
    - expect: The 'Login' button becomes enabled

#### 1.4. Login with invalid API key shows error

**File:** `tests/web/auth/login-invalid-key.spec.ts`

**Steps:**

  1. Navigate to <http://localhost:3007/login>. Type 'dk_invalid_key_12345' into the API Key field.
    - expect: The 'Login' button becomes enabled
  2. Click the 'Login' button.
    - expect: An error message 'Invalid API key' appears below the Login button
    - expect: The page remains on <http://localhost:3007/login>
    - expect: The API key field retains the entered value
    - expect: No console content or navigation is shown
  3. Verify no sensitive information is exposed in the error message.
    - expect: The error message only says 'Invalid API key' without any technical details about the backend or key format requirements

#### 1.5. Login with valid non-admin API key is rejected

**File:** `tests/web/auth/login-non-admin-key.spec.ts`

**Steps:**

  1. Using the admin key, navigate to the API Keys page (/keys) and create a new key with only the 'Read Jobs' scope selected. Record the full key value from the one-time display modal.
    - expect: The key is created successfully and the full key value is displayed
  2. Log out and navigate to <http://localhost:3007/login>. Enter the newly created jobs:read-only key into the API Key field and click 'Login'.
    - expect: An error message appears indicating the key does not have admin scope (e.g., 'API key does not have admin scope')
    - expect: The page remains on /login
    - expect: Access to the console is denied

#### 1.6. Login with valid admin API key grants access

**File:** `tests/web/auth/login-admin-key.spec.ts`

**Steps:**

  1. Navigate to <http://localhost:3007/login>. Type the admin API key 'dk_PE2-k0faXI3JBhW-tYWqPPzbJxpqlWHsXG_SMNZU8bo' into the API Key field and click 'Login'.
    - expect: The login succeeds without an error message
    - expect: The browser navigates to <http://localhost:3007/> (Dashboard)
    - expect: The sidebar is visible with navigation links for Dashboard, Batch Jobs, Real-time, Engines, Models, API Keys, Webhooks, Audit Log, and Settings
    - expect: A masked key prefix (e.g., 'dk_PE2-k0f...') is shown at the bottom of the sidebar
    - expect: A 'Logout' button is visible in the sidebar

#### 1.7. API key input field is masked as password type

**File:** `tests/web/auth/login-field-masking.spec.ts`

**Steps:**

  1. Navigate to <http://localhost:3007/login>. Inspect the API Key input field type attribute.
    - expect: The input field has type='password', meaning the entered characters are masked and not visible in plaintext in the UI

#### 1.8. Logout clears session and redirects to login

**File:** `tests/web/auth/logout.spec.ts`

**Steps:**

  1. Log in with the admin API key. Confirm the Dashboard is displayed.
    - expect: The Dashboard page is visible at <http://localhost:3007/>
  2. Click the 'Logout' button in the sidebar.
    - expect: The browser navigates to <http://localhost:3007/login>
    - expect: The login page is displayed with an empty API Key field
    - expect: No dashboard or console content is visible
  3. After logout, navigate directly to <http://localhost:3007/keys>.
    - expect: The browser redirects to <http://localhost:3007/login>, confirming the session has been fully cleared
  4. After logout, check the browser's sessionStorage for any stored API key.
    - expect: The key 'dalston_api_key' is no longer present in sessionStorage, confirming the API key was removed from client-side storage on logout

#### 1.9. Session does not persist across browser tab closure

**File:** `tests/web/auth/session-persistence.spec.ts`

**Steps:**

  1. Log in with the admin API key. Confirm the Dashboard is displayed.
    - expect: The Dashboard is visible
  2. Close the browser tab and open a new tab. Navigate to <http://localhost:3007/>.
    - expect: The browser redirects to /login because sessionStorage is not shared across new tabs/windows or after tab closure, meaning the session does not persist

### 2. API Key Management Security

**Seed:** ``

#### 2.1. API keys are masked in the list view

**File:** `tests/web/api-keys/key-masking.spec.ts`

**Steps:**

  1. Log in with the admin key. Navigate to /keys.
    - expect: All API key values in the Prefix column are shown as truncated prefixes (e.g., 'dk_PE2-k0f...') - no full key values are displayed
  2. Observe the currently active session key in the list.
    - expect: The current key row displays a 'current' badge next to the masked prefix
    - expect: The full key value is never visible in the list

#### 2.2. New API key full value is shown only once at creation

**File:** `tests/web/api-keys/key-one-time-display.spec.ts`

**Steps:**

  1. Log in with the admin key. Navigate to /keys and click 'Create Key'.
    - expect: The Create API Key modal opens
  2. Enter a name such as 'Security Test Key', select only 'Read Jobs' scope, and click 'Create Key'.
    - expect: A success modal titled 'API Key Created' appears
    - expect: A warning banner states 'Save this key now - This is the only time you will see the full API key'
    - expect: The key is initially shown in a masked format
  3. Click the eye/reveal icon in the key display area.
    - expect: The full API key value is revealed
  4. Click 'Done' to close the modal. Find the newly created key in the list.
    - expect: The key appears in the list with only its prefix shown (e.g., 'dk_xxxxx...'), confirming the full key cannot be retrieved again through the UI
  5. Revoke the test key using its revoke button to clean up.
    - expect: A confirmation dialog appears: 'Are you sure you want to revoke this API key? This action cannot be undone.'
    - expect: After confirming, the key is removed from the list

#### 2.3. Admin scope is mutually exclusive with other scopes

**File:** `tests/web/api-keys/admin-scope-exclusive.spec.ts`

**Steps:**

  1. Log in with the admin key. Navigate to /keys and click 'Create Key'. Observe the default state of the form.
    - expect: 'Read Jobs', 'Create Jobs', and 'Real-time' scopes are checked by default
    - expect: 'Webhooks' and 'Admin Access' are unchecked
  2. Check the 'Admin Access' checkbox.
    - expect: All other scope checkboxes ('Read Jobs', 'Create Jobs', 'Real-time', 'Webhooks') are unchecked and deselected
    - expect: Only 'Admin Access' remains checked
    - expect: An orange warning banner appears: 'Admin scope selected - This key will have full access to all API operations including key management.'
  3. With Admin Access checked, attempt to check 'Read Jobs'.
    - expect: Checking 'Read Jobs' clears the 'Admin Access' selection and activates the individual scope, enforcing the mutual exclusivity rule
  4. Click Cancel to close the modal without creating a key.
    - expect: The modal closes and no key is created

#### 2.4. Cannot remove all scopes from a key in the creation form

**File:** `tests/web/api-keys/minimum-scope-enforcement.spec.ts`

**Steps:**

  1. Log in with the admin key. Navigate to /keys and click 'Create Key'. The default scopes are 'Read Jobs', 'Create Jobs', and 'Real-time'.
    - expect: Three scopes are checked by default
  2. Uncheck 'Create Jobs' and then 'Real-time', leaving only 'Read Jobs' checked. Attempt to uncheck 'Read Jobs'.
    - expect: The 'Read Jobs' checkbox cannot be unchecked when it is the last remaining scope, enforcing a minimum of one scope

#### 2.5. Key revocation requires confirmation

**File:** `tests/web/api-keys/revoke-confirmation.spec.ts`

**Steps:**

  1. Log in with the admin key. Navigate to /keys. Click the revoke button (trash/delete icon) for any non-current key.
    - expect: A confirmation dialog appears with: heading 'Revoke API Key', text 'Are you sure you want to revoke this API key? This action cannot be undone.', the masked key prefix, the key name, and 'Cancel' and 'Revoke Key' buttons
  2. Click 'Cancel' in the confirmation dialog.
    - expect: The dialog closes without revoking the key
    - expect: The key remains in the list with the same status

#### 2.6. Rate limit can be set per API key

**File:** `tests/web/api-keys/rate-limit-setting.spec.ts`

**Steps:**

  1. Log in with the admin key. Navigate to /keys and click 'Create Key'. Enter a name 'Rate Limited Key' and set the Rate Limit field to 10.
    - expect: The Rate Limit field accepts numeric input
    - expect: The unit label 'requests/minute' is displayed next to the input
  2. Click 'Create Key' to submit the form.
    - expect: The key is created with the specified rate limit
    - expect: The success modal is shown with key details
  3. Revoke the created key to clean up.
    - expect: The key is successfully revoked

#### 2.7. API key list shows scopes for each key

**File:** `tests/web/api-keys/scope-display.spec.ts`

**Steps:**

  1. Log in with the admin key. Navigate to /keys.
    - expect: The key list table has a 'Scopes' column
    - expect: Each key row displays all of its assigned scopes as individual badges/tags
    - expect: Admin keys show 'admin' scope badge
    - expect: Non-admin keys show their individual scopes (e.g., 'jobs:read', 'jobs:write', 'webhooks')
  2. Verify the key used for authentication ('dk_PE2-k0f...') shows the 'admin' scope.
    - expect: The current key row shows 'admin' in the Scopes column and has a 'current' marker

#### 2.8. API key list shows last used timestamp

**File:** `tests/web/api-keys/last-used-tracking.spec.ts`

**Steps:**

  1. Log in with the admin key. Navigate to /keys.
    - expect: The key list table has a 'Last Used' column
    - expect: The currently active key shows a recent timestamp (e.g., 'just now')
    - expect: Keys that have never been used show 'Never' in the Last Used column

### 3. Authorization and Access Control

**Seed:** ``

#### 3.1. Admin key grants access to all navigation sections

**File:** `tests/web/authorization/admin-full-access.spec.ts`

**Steps:**

  1. Log in with the admin API key. Observe the sidebar navigation.
    - expect: All nine navigation links are visible: Dashboard, Batch Jobs, Real-time, Engines, Models, API Keys, Webhooks, Audit Log, Settings
  2. Click each navigation link in order and verify the page loads.
    - expect: Dashboard (/) loads with system status widgets and recent jobs
    - expect: Batch Jobs (/jobs) loads with job list
    - expect: Real-time (/realtime) loads with sessions list
    - expect: Engines (/engines) loads with engine list
    - expect: Models (/models) loads with model catalog
    - expect: API Keys (/keys) loads with key management table
    - expect: Webhooks (/webhooks) loads with webhook endpoints
    - expect: Audit Log (/audit) loads with event table
    - expect: Settings (/settings) loads with configuration tabs

#### 3.2. Console requires admin scope - non-admin keys cannot log in

**File:** `tests/web/authorization/non-admin-rejected.spec.ts`

**Steps:**

  1. Log in with the admin key. Navigate to /keys and create a key with only 'jobs:read' scope. Record the full key from the one-time modal.
    - expect: Key is created successfully with jobs:read scope only
  2. Log out. Navigate to /login. Enter the jobs:read key and click Login.
    - expect: Login fails with an error message indicating the key does not have admin scope
    - expect: Access to the console is denied
    - expect: The page stays at /login

#### 3.3. All console API endpoints require admin authorization

**File:** `tests/web/authorization/api-endpoint-auth.spec.ts`

**Steps:**

  1. In a fresh browser session (no authentication), attempt to make a direct HTTP GET request to /api/console/dashboard.
    - expect: The server returns a 401 or 403 HTTP status code
    - expect: No dashboard data is returned to the unauthenticated client
  2. Attempt to make a direct HTTP GET request to /api/console/jobs without authentication.
    - expect: The server returns a 401 or 403 HTTP status code
  3. Attempt to make a direct HTTP GET request to /auth/keys without authentication.
    - expect: The server returns a 401 or 403 HTTP status code
    - expect: No API key data is returned
  4. Attempt to make a direct HTTP GET request to /v1/audit without authentication.
    - expect: The server returns a 401 or 403 HTTP status code

#### 3.4. System settings expose infrastructure details only when authenticated

**File:** `tests/web/authorization/system-settings-auth.spec.ts`

**Steps:**

  1. Log in with the admin key. Navigate to /settings and click the 'System' tab.
    - expect: The System tab is visible and accessible
    - expect: Infrastructure details are shown: Redis URL, Database connection string (with password masked as ****), S3 Bucket, S3 Region, and Version
    - expect: Each setting has a copy-to-clipboard button
  2. Verify the Database connection string masks the password.
    - expect: The password in the database URL is shown as '**** ' (e.g., 'postgresql+asyncpg://dalston:****@postgres:5432/dalston'), confirming sensitive credentials are not fully exposed
  3. Log out and attempt to access /api/console/settings directly.
    - expect: The server returns a 401 or 403 status code, confirming unauthenticated access is denied

### 4. Webhook Security

**Seed:** ``

#### 4.1. Webhook signing secret is shown only once at creation

**File:** `tests/web/webhooks/secret-one-time-display.spec.ts`

**Steps:**

  1. Log in with the admin key. Navigate to /webhooks and click 'Create Webhook'.
    - expect: The Create Webhook Endpoint modal opens with URL, Description, and Events fields
  2. Enter a test URL (e.g., '<https://example.com/webhook>'), leave the default events selected, and click 'Create Webhook'.
    - expect: A success modal titled 'Webhook Created' appears
    - expect: An orange warning banner states 'Save this secret now - This is the only time you will see the signing secret. Store it securely!'
    - expect: The signing secret is displayed in masked format by default
  3. Click the reveal (eye) icon to show the full signing secret.
    - expect: The full signing secret value is displayed
  4. Click the copy icon to copy the signing secret.
    - expect: The copy icon briefly changes to a checkmark, confirming the value was copied to the clipboard
  5. Click 'Done' to close the modal. Find the newly created webhook in the list. Look for any way to view the signing secret again.
    - expect: The full signing secret is not accessible from the webhook list or detail view, confirming it is only shown once

#### 4.2. Webhook signing secret rotation shows new secret once

**File:** `tests/web/webhooks/secret-rotation.spec.ts`

**Steps:**

  1. Log in with the admin key. Navigate to /webhooks. Open an existing webhook's detail page by clicking on it.
    - expect: The webhook detail page is displayed with endpoint information
  2. Locate and click the 'Rotate Secret' option for the webhook.
    - expect: A success modal titled 'Secret Rotated' appears
    - expect: The warning message reads 'Save this secret now - This is the only time you will see the signing secret. Store it securely! The old secret is now invalid.'
    - expect: The new signing secret is shown in the modal
  3. Click 'Done' and verify the old secret can no longer be retrieved.
    - expect: The new secret is the only valid signing secret, and the full secret value cannot be retrieved from the UI again

#### 4.3. Webhook URL must use HTTPS

**File:** `tests/web/webhooks/https-requirement.spec.ts`

**Steps:**

  1. Log in with the admin key. Navigate to /webhooks and click 'Create Webhook'. Observe the URL field helper text.
    - expect: The helper text below the URL field reads 'Must be HTTPS in production. Webhook payloads will be POSTed here.'
  2. Enter an HTTP (non-HTTPS) URL such as '<http://example.com/webhook>' and attempt to create the webhook.
    - expect: A validation error is shown or the creation fails, preventing an insecure HTTP endpoint from being registered for production use

#### 4.4. Webhook signature verification information is provided

**File:** `tests/web/webhooks/signature-verification-info.spec.ts`

**Steps:**

  1. Log in with the admin key. Navigate to /webhooks and create a new webhook endpoint. In the resulting secret modal, scroll down.
    - expect: A section titled 'Signature Verification (Standard Webhooks)' is visible
    - expect: The verification example shows the HMAC-SHA256 signing scheme with webhook-id, webhook-timestamp, and webhook-signature headers
    - expect: The signing formula is documented: HMAC-SHA256('{msg_id}.{timestamp}.{body}')

#### 4.5. Webhook deletion requires destructive action

**File:** `tests/web/webhooks/delete-confirmation.spec.ts`

**Steps:**

  1. Log in with the admin key. Navigate to /webhooks. If a webhook endpoint exists, click the delete action for that endpoint.
    - expect: A confirmation dialog or destructive action is required before the webhook is deleted
    - expect: Cancelling the confirmation does not delete the webhook

### 5. Audit Log Security

**Seed:** ``

#### 5.1. Audit log records actor identity for all events

**File:** `tests/web/audit/actor-identity.spec.ts`

**Steps:**

  1. Log in with the admin key. Navigate to /audit.
    - expect: The audit log table is visible with columns: Timestamp, Action, Resource, Actor, and IP Address
    - expect: Each event row shows the API key prefix of the actor who performed the action (e.g., 'dk_PE2-k0f')
    - expect: Each event row shows the source IP address of the request
  2. Verify that resource links in audit events are clickable.
    - expect: Clicking a job resource link (e.g., 'f5dee753...') navigates to the corresponding job detail page at /jobs/{id}

#### 5.2. Audit log can be filtered by actor ID

**File:** `tests/web/audit/filtering.spec.ts`

**Steps:**

  1. Log in with the admin key. Navigate to /audit. Click the 'Filters' button.
    - expect: A filter panel expands showing fields: Resource Type (dropdown), Action (text), Actor ID (text), Since (datetime), Until (datetime), Sort order, and Rows per page
  2. Enter 'dk_PE2-k0f' in the 'Actor ID' filter field and apply the filter.
    - expect: The audit log is filtered to show only events performed by the key with that prefix
  3. Enter 'job.created' in the 'Action' filter field.
    - expect: The audit log filters to show only job.created events

#### 5.3. Audit log captures IP address of each request

**File:** `tests/web/audit/ip-tracking.spec.ts`

**Steps:**

  1. Log in with the admin key. Create a new transcription job or perform any auditable action. Navigate to /audit.
    - expect: The most recent audit event shows the source IP address in the 'IP Address' column
    - expect: The IP address corresponds to the client address that performed the action

#### 5.4. Audit log entries are read-only

**File:** `tests/web/audit/read-only.spec.ts`

**Steps:**

  1. Log in with the admin key. Navigate to /audit. Attempt to edit, delete, or modify any audit log entry.
    - expect: No edit, delete, or modification controls are present in the audit log UI
    - expect: Audit events cannot be altered through the console interface

### 6. Security Indicators and UI Hardening

**Seed:** ``

#### 6.1. Active session key is visually identified in the sidebar

**File:** `tests/web/security-ui/active-key-display.spec.ts`

**Steps:**

  1. Log in with the admin key. Observe the bottom of the sidebar.
    - expect: The masked API key prefix (e.g., 'dk_PE2-k0f...') is displayed in a monospace font at the bottom of the sidebar, reminding the user which key is active
    - expect: The full key value is never shown in the sidebar

#### 6.2. Active session key is marked 'current' in the API Keys list

**File:** `tests/web/security-ui/current-key-badge.spec.ts`

**Steps:**

  1. Log in with the admin key. Navigate to /keys.
    - expect: The key currently being used for the session has a 'current' badge displayed next to its prefix in the Prefix column
    - expect: This makes it clear which key is actively in use and prevents the user from accidentally revoking their own session key

#### 6.3. Rate limit configuration is exposed in Settings

**File:** `tests/web/security-ui/rate-limit-settings.spec.ts`

**Steps:**

  1. Log in with the admin key. Navigate to /settings. The 'Rate Limits' tab is selected by default.
    - expect: Three rate limit settings are visible: 'Maximum API requests per minute per tenant' (default 600), 'Maximum concurrent batch transcription jobs per tenant' (default 10), and 'Maximum concurrent realtime WebSocket sessions per tenant' (default 5)
    - expect: Each setting displays the corresponding environment variable name (e.g., DALSTON_RATE_LIMIT_REQUESTS_PER_MINUTE)

#### 6.4. Data retention settings are configurable

**File:** `tests/web/security-ui/retention-settings.spec.ts`

**Steps:**

  1. Log in with the admin key. Navigate to /settings and click the 'Retention' tab.
    - expect: Retention settings are visible: cleanup interval, max jobs per cleanup sweep, and default retention days
    - expect: The default retention is 30 days

#### 6.5. System settings are read-only (environment variable controlled)

**File:** `tests/web/security-ui/system-settings-readonly.spec.ts`

**Steps:**

  1. Log in with the admin key. Navigate to /settings and click the 'System' tab.
    - expect: A notice states 'System settings are read-only and controlled by environment variables.'
    - expect: The Redis URL, Database, S3 Bucket, S3 Region, and Version fields are displayed as read-only values
    - expect: No input fields or edit buttons are present for system settings
    - expect: Each setting has a copy-to-clipboard button for convenience

#### 6.6. Admin scope warning is displayed during key creation

**File:** `tests/web/security-ui/admin-scope-warning.spec.ts`

**Steps:**

  1. Log in with the admin key. Navigate to /keys and click 'Create Key'. Check the 'Admin Access' checkbox.
    - expect: An orange warning banner appears immediately upon selecting the Admin Access scope
    - expect: The warning reads 'Admin scope selected - This key will have full access to all API operations including key management.'
    - expect: A warning triangle icon is displayed next to 'Admin Access' in the scope list

#### 6.7. API key display in usage example uses masked value by default

**File:** `tests/web/security-ui/masked-key-in-usage-example.spec.ts`

**Steps:**

  1. Log in with the admin key. Navigate to /keys and click 'Create Key'. Create any key. In the success modal, observe the 'Usage' section.
    - expect: The curl example command shows the masked key format (e.g., 'dk_xxxxx...xxxx') by default, not the full key value
    - expect: The full key only appears in the curl example after clicking the reveal icon

### 7. Session and Storage Security

**Seed:** ``

#### 7.1. API key is stored in sessionStorage, not localStorage

**File:** `tests/web/session/storage-mechanism.spec.ts`

**Steps:**

  1. Log in with the admin key. Open the browser's developer tools and inspect the sessionStorage.
    - expect: The key 'dalston_api_key' is present in sessionStorage containing the API key value
  2. Inspect the browser's localStorage.
    - expect: The key 'dalston_api_key' is NOT present in localStorage, confirming the session is limited to the current tab and is not persisted across browser sessions

#### 7.2. API key is sent as Bearer token in Authorization header

**File:** `tests/web/session/bearer-token-auth.spec.ts`

**Steps:**

  1. Log in with the admin key. Navigate to the Dashboard. Open browser developer tools and inspect network requests.
    - expect: All API requests to backend endpoints (e.g., /api/console/dashboard, /auth/keys) include an 'Authorization: Bearer dk_PE2-k0f...' header
    - expect: The API key is never appended as a URL query parameter, preventing it from appearing in server logs or browser history

#### 7.3. File downloads use authenticated fetch, not exposed URL parameters

**File:** `tests/web/session/authenticated-downloads.spec.ts`

**Steps:**

  1. Log in with the admin key. Navigate to /jobs. Open an existing completed job and trigger a transcript export (e.g., download as SRT or JSON).
    - expect: The download is initiated without the API key appearing in the URL as a query parameter
    - expect: Network inspection shows the download request includes the Authorization Bearer header
    - expect: The downloaded file is saved with the job ID as the filename
