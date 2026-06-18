from app.siftarr.models.request import (
    ACTIVE_STAGING_WORKFLOW_STATUSES,
    AVAILABILITY_SAFE_REQUEST_STATUSES,
    MUTABLE_REQUEST_STATUSES,
    NON_TERMINAL_REQUEST_STATUSES,
    RESETTABLE_EPISODE_DOWNLOAD_STATUSES,
    TERMINAL_REQUEST_STATUSES,
    RequestStatus,
    is_active_staging_workflow_status,
    is_mutable_request_status,
    is_terminal_request_status,
)


def test_lifecycle_status_sets_preserve_existing_groupings() -> None:
    assert ACTIVE_STAGING_WORKFLOW_STATUSES == (
        RequestStatus.STAGED,
        RequestStatus.DOWNLOADING,
    )
    assert NON_TERMINAL_REQUEST_STATUSES == (
        RequestStatus.SEARCHING,
        RequestStatus.PENDING,
        RequestStatus.UNRELEASED,
        RequestStatus.STAGED,
        RequestStatus.DOWNLOADING,
    )
    assert TERMINAL_REQUEST_STATUSES == (
        RequestStatus.COMPLETED,
        RequestStatus.FAILED,
        RequestStatus.DENIED,
    )
    assert MUTABLE_REQUEST_STATUSES == (
        RequestStatus.SEARCHING,
        RequestStatus.PENDING,
        RequestStatus.UNRELEASED,
        RequestStatus.STAGED,
        RequestStatus.DOWNLOADING,
        RequestStatus.FAILED,
    )
    assert RESETTABLE_EPISODE_DOWNLOAD_STATUSES == (
        RequestStatus.DOWNLOADING,
        RequestStatus.STAGED,
        RequestStatus.SEARCHING,
    )
    assert AVAILABILITY_SAFE_REQUEST_STATUSES == (
        RequestStatus.UNRELEASED,
        RequestStatus.COMPLETED,
    )


def test_lifecycle_status_predicates_accept_enum_and_string() -> None:
    assert is_active_staging_workflow_status(RequestStatus.STAGED)
    assert is_active_staging_workflow_status("downloading")
    assert is_terminal_request_status("completed")
    assert is_mutable_request_status(RequestStatus.FAILED)
    assert not is_mutable_request_status(RequestStatus.DENIED)
    assert not is_terminal_request_status("unknown")
