# M45 Security Hardening - End-to-End Test Plan

## Overview

This test plan validates the security hardening implementation across three layers:

1. Direct API calls (curl)
2. CLI utility
3. Web console (Playwright)

---

## Part 1: API Security Tests

### 1.1 Authentication Tests (401 Unauthorized)

| Test | Endpoint | Method | Expected |
|------|----------|--------|----------|
| No auth header | `/v1/audio/transcriptions` | GET | 401 |
| Invalid key format | `/v1/audio/transcriptions` | GET | 401 |
| Revoked key | `/v1/audio/transcriptions` | GET | 401 |
| Expired key | `/v1/audio/transcriptions` | GET | 401 |

### 1.2 Authorization Tests (403 Forbidden)

| Test | Endpoint | Method | Scope | Expected |
|------|----------|--------|-------|----------|
| Model pull without admin | `/v1/models/{id}/pull` | POST | jobs:write | 403 |
| Model delete without admin | `/v1/models/{id}` | DELETE | jobs:write | 403 |
| Create API key without admin | `/auth/keys` | POST | jobs:write | 403 |
| Console access without admin | `/api/console/dashboard` | GET | jobs:write | 403 |

### 1.3 Ownership Tests (404 Anti-enumeration)

| Test | Description | Expected |
|------|-------------|----------|
| Access job created by different key | Non-admin key B accesses key A's job | 404 |
| Admin access any job | Admin key accesses any tenant job | 200 |
| Cross-tenant access | Key from tenant B accesses tenant A job | 404 |

### 1.4 Public Endpoints (No Auth Required)

| Endpoint | Method | Expected |
|----------|--------|----------|
| `/health` | GET | 200 |
| `/healthz` | GET | 200 |
| `/ready` | GET | 200 |
| `/docs` | GET | 200 |
| `/openapi.json` | GET | 200 |

---

## Part 2: CLI Security Tests

### 2.1 Key Management

| Test | Command | Expected |
|------|---------|----------|
| Create key with admin | `dalston auth create-key` | Success |
| Create key without admin | `dalston auth create-key` (non-admin) | 403 |
| List keys | `dalston auth list-keys` | Success |
| Revoke key | `dalston auth revoke-key` | Success |

### 2.2 Job Operations with Scopes

| Test | Scope | Command | Expected |
|------|-------|---------|----------|
| Create job | jobs:write | `dalston transcribe` | Success |
| Create job | jobs:read | `dalston transcribe` | 403 |
| List jobs | jobs:read | `dalston jobs list` | Success |
| Delete job | jobs:write | `dalston jobs delete` | Success |

---

## Part 3: Web Console Security (Playwright)

### 3.1 Authentication Flow

| Test | Scenario | Expected |
|------|----------|----------|
| Access without login | Navigate to /console | Redirect to login |
| Login with invalid key | Enter invalid API key | Error message |
| Login with valid key | Enter valid API key | Dashboard loads |
| Session expiry | Wait for token TTL | Redirect to login |

### 3.2 Authorization in Console

| Test | User Role | Action | Expected |
|------|-----------|--------|----------|
| View dashboard | Admin | Access /console/dashboard | Success |
| View dashboard | Non-admin | Access /console/dashboard | 403 or redirect |
| Delete job | Admin | Click delete button | Success |
| Update settings | Admin | Modify settings | Success |
| Update settings | Non-admin | Modify settings | 403 |

### 3.3 UI Security Indicators

| Test | Check | Expected |
|------|-------|----------|
| Key scope display | Login with limited scopes | Show scope badges |
| Admin indicator | Login as admin | Show admin badge |
| Disabled actions | Login as non-admin | Mutation buttons disabled |

---

## Execution Steps

1. Start local development stack
2. Create test API keys with different scopes
3. Execute Part 1 tests via curl
4. Execute Part 2 tests via CLI (if available)
5. Execute Part 3 tests via Playwright
6. Document results
