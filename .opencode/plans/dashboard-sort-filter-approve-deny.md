# Dashboard Sort, Filter, Approve & Deny Implementation Plan

## Overview
Implement sorting, filtering, and approve/deny functionality for the dashboard to allow users to manage requests directly from the dashboard.

## Requirements Summary
- **Sorting**: All columns sortable (Title, Type, Status, Requested Date, Requested By)
- **Filtering**: Text-based filtering across all columns
- **Approve**: Approve request in Overseerr via API + trigger local search
- **Deny**: Decline request in Overseerr via API + mark as failed locally
- **Overseerr Status Column**: Show Overseerr approval status (only if not already approved/denied)
- **Testing**: Add unit tests for new functionality (no integration tests)

---

## Phase 1: Client-Side Sorting & Filtering
**File:** `app/arbitratarr/templates/dashboard.html`

## Phase 2: OverseerrService Enhancement
**File:** `app/arbitratarr/services/overseerr_service.py`

## Phase 3: Database Model Update
**File:** `app/arbitratarr/models/request.py`

## Phase 4: Dashboard Router Endpoints
**File:** `app/arbitratarr/routers/dashboard.py`

## Phase 5: Dashboard Template Update
**File:** `app/arbitratarr/templates/dashboard.html`

## Phase 6: Webhook Handler Update
**File:** `app/arbitratarr/routers/webhooks.py`

## Phase 7: Tests for OverseerrService
**File:** `tests/test_overseerr_service.py`

## Phase 8: Tests for Dashboard Router
**File:** `tests/test_dashboard_router.py` (new file)

---

## Commit Schedule
- Phase 1: Commit "Add client-side sorting and filtering to dashboard"
- Phase 2: Commit "Add approve/decline methods to OverseerrService"
- Phase 3: Commit "Add overseerr_request_id field to Request model"
- Phase 4: Commit "Add approve/deny endpoints to dashboard router"
- Phase 5: Commit "Update dashboard template with action buttons"
- Phase 6: Commit "Store overseerr_request_id in webhook handler"
- Phase 7: Commit "Add tests for OverseerrService approve/decline"
- Phase 8: Commit "Add tests for dashboard approve/deny endpoints"
