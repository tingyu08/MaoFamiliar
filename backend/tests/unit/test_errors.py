from datetime import timedelta

import pytest

from familiar.gemini.errors import ErrorKind, classify, parse_retry_after


class FakeApiError(Exception):
    def __init__(self, status_code: int | None, message: str):
        super().__init__(message)
        self.status_code = status_code


class APITimeoutError(Exception):
    pass


DAILY = ("Error code: 429 - {'error': {'message': 'Rate limit exceeded for model gemini-4-hydrogen "
         "(limit: 20 requests per day on Free Tier). Please retry in 59m51s or upgrade'}}")
MINUTE_TOKENS = ("Error code: 429 - Rate limit exceeded for model gemini-decision-1 "
                 "(limit: 50000 input tokens per minute on Free Tier). Please retry in 20s")


class TestClassify:
    def test_daily_quota(self):
        error = classify(FakeApiError(429, DAILY))
        assert error.kind is ErrorKind.DAILY_QUOTA
        assert error.retry_after == timedelta(minutes=59, seconds=51)

    def test_minute_quota(self):
        error = classify(FakeApiError(429, MINUTE_TOKENS))
        assert error.kind is ErrorKind.MINUTE_QUOTA
        assert error.retry_after == timedelta(seconds=20)

    def test_429_without_detail_is_minute(self):
        assert classify(FakeApiError(429, "too many")).kind is ErrorKind.MINUTE_QUOTA

    @pytest.mark.parametrize("status", [500, 502, 503, 504])
    def test_server_errors_are_overloaded(self, status):
        assert classify(FakeApiError(status, "high demand")).kind is ErrorKind.OVERLOADED

    def test_safety_block(self):
        message = "Error code: 400 - Request blocked due to safety violations (harmful content)."
        assert classify(FakeApiError(400, message)).kind is ErrorKind.SAFETY_BLOCKED

    def test_other_bad_request(self):
        assert classify(FakeApiError(400, "invalid argument")).kind is ErrorKind.BAD_REQUEST

    def test_timeout_by_class_name(self):
        assert classify(APITimeoutError("timed out")).kind is ErrorKind.TIMEOUT

    def test_unknown(self):
        error = classify(RuntimeError("boom"))
        assert error.kind is ErrorKind.OTHER
        assert error.message == "boom"

    def test_status_from_response_attribute(self):
        class WithResponse(Exception):
            response = type("R", (), {"status_code": 503})()

        assert classify(WithResponse("x")).kind is ErrorKind.OVERLOADED

    def test_message_is_truncated(self):
        assert len(classify(RuntimeError("x" * 5000)).message) <= 500


class TestParseRetryAfter:
    @pytest.mark.parametrize("text,expected", [
        ("Please retry in 59m51s", timedelta(minutes=59, seconds=51)),
        ("retry in 20s", timedelta(seconds=20)),
        ("retry in 1h2m3s", timedelta(hours=1, minutes=2, seconds=3)),
        ("retry in 1.5s", timedelta(seconds=1.5)),
    ])
    def test_parses(self, text, expected):
        assert parse_retry_after(text) == expected

    @pytest.mark.parametrize("text", ["no hint here", "retry in soon"])
    def test_missing(self, text):
        assert parse_retry_after(text) is None
