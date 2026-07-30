from core.errors import AppError, ErrorCode


def test_app_error_preserves_public_metadata() -> None:
    error = AppError(
        code=ErrorCode.OPERATION_TIMED_OUT,
        message="The recipe generation timed out.",
        status_code=504,
        retryable=True,
        details={"timeout_seconds": 300},
        session_id="session-1",
        job_id="job-1",
    )

    assert error.code is ErrorCode.OPERATION_TIMED_OUT
    assert error.message == "The recipe generation timed out."
    assert error.status_code == 504
    assert error.retryable is True
    assert error.details == {"timeout_seconds": 300}
    assert error.session_id == "session-1"
    assert error.job_id == "job-1"


def test_app_error_uses_an_empty_details_mapping_by_default() -> None:
    error = AppError(
        code=ErrorCode.INVALID_REQUEST,
        message="The request is invalid.",
        status_code=422,
        retryable=False,
    )

    assert error.details == {}
