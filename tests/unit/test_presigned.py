"""Unit tests for M77 presigned URL generation and HTTP transport."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from dalston.common.presigned import _parse_s3_uri, generate_get_url, generate_put_url
from dalston.engine_sdk.http import EngineTransportError, fetch_json, put_json

# =============================================================================
# presigned.py tests
# =============================================================================


class TestParseS3Uri:
    def test_standard_uri(self):
        bucket, key = _parse_s3_uri("s3://my-bucket/path/to/object.json")
        assert bucket == "my-bucket"
        assert key == "path/to/object.json"

    def test_nested_key(self):
        bucket, key = _parse_s3_uri(
            "s3://dalston-artifacts/jobs/abc/tasks/xyz/input.json"
        )
        assert bucket == "dalston-artifacts"
        assert key == "jobs/abc/tasks/xyz/input.json"

    def test_non_s3_uri_raises(self):
        with pytest.raises(ValueError, match="Not an S3 URI"):
            _parse_s3_uri("https://example.com/file.json")


class TestGenerateGetUrl:
    def test_returns_url_with_correct_bucket_and_key(self, monkeypatch):
        fake_url = "http://minio:9000/dalston-artifacts/jobs/j1/tasks/t1/input.json?X-Amz-Signature=abc"
        mock_client = MagicMock()
        mock_client.generate_presigned_url.return_value = fake_url

        monkeypatch.setenv("DALSTON_S3_ENDPOINT_URL", "http://minio:9000")
        monkeypatch.setenv("DALSTON_S3_REGION", "eu-west-2")

        with patch("dalston.common.presigned.boto3.client", return_value=mock_client):
            url = generate_get_url(
                "s3://dalston-artifacts/jobs/j1/tasks/t1/input.json", ttl_seconds=300
            )

        assert url == fake_url
        mock_client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={
                "Bucket": "dalston-artifacts",
                "Key": "jobs/j1/tasks/t1/input.json",
            },
            ExpiresIn=300,
        )

    def test_endpoint_url_passed_to_client(self, monkeypatch):
        monkeypatch.setenv("DALSTON_S3_ENDPOINT_URL", "http://minio:9000")
        monkeypatch.delenv("DALSTON_S3_REGION", raising=False)

        mock_client = MagicMock()
        mock_client.generate_presigned_url.return_value = (
            "http://minio:9000/bucket/key?sig=x"
        )

        with patch(
            "dalston.common.presigned.boto3.client", return_value=mock_client
        ) as mock_boto:
            generate_get_url("s3://bucket/key")

        _, kwargs = mock_boto.call_args
        assert kwargs.get("endpoint_url") == "http://minio:9000"

    def test_no_endpoint_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("DALSTON_S3_ENDPOINT_URL", raising=False)

        mock_client = MagicMock()
        mock_client.generate_presigned_url.return_value = (
            "https://s3.amazonaws.com/bucket/key?sig=x"
        )

        with patch(
            "dalston.common.presigned.boto3.client", return_value=mock_client
        ) as mock_boto:
            generate_get_url("s3://bucket/key")

        _, kwargs = mock_boto.call_args
        assert "endpoint_url" not in kwargs


class TestGeneratePutUrl:
    def test_uses_put_object_operation(self, monkeypatch):
        monkeypatch.delenv("DALSTON_S3_ENDPOINT_URL", raising=False)

        mock_client = MagicMock()
        mock_client.generate_presigned_url.return_value = "https://example.com/put-url"

        with patch("dalston.common.presigned.boto3.client", return_value=mock_client):
            generate_put_url("s3://my-bucket/jobs/j/tasks/t/output.json")

        mock_client.generate_presigned_url.assert_called_once_with(
            "put_object",
            Params={"Bucket": "my-bucket", "Key": "jobs/j/tasks/t/output.json"},
            ExpiresIn=604800,
        )


# =============================================================================
# http.py tests
# =============================================================================


class TestFetchJson:
    def test_success(self, respx_mock=None):
        payload = {"task_id": "t1", "job_id": "j1"}

        with patch("dalston.engine_sdk.http.httpx.Client") as mock_cls:
            mock_response = MagicMock()
            mock_response.is_success = True
            mock_response.json.return_value = payload
            mock_cls.return_value.__enter__.return_value.get.return_value = (
                mock_response
            )

            result = fetch_json("http://minio:9000/bucket/input.json?sig=x")

        assert result == payload

    def test_permanent_4xx_raises_immediately(self):
        with patch("dalston.engine_sdk.http.httpx.Client") as mock_cls:
            mock_response = MagicMock()
            mock_response.is_success = False
            mock_response.status_code = 403
            mock_cls.return_value.__enter__.return_value.get.return_value = (
                mock_response
            )

            with pytest.raises(EngineTransportError) as exc_info:
                fetch_json("http://example.com/expired-url")

        assert exc_info.value.status_code == 403
        # Should not retry — only one attempt
        assert mock_cls.return_value.__enter__.return_value.get.call_count == 1

    def test_5xx_retries_then_raises(self):
        with (
            patch("dalston.engine_sdk.http.httpx.Client") as mock_cls,
            patch("dalston.engine_sdk.http.time.sleep"),
        ):
            mock_response = MagicMock()
            mock_response.is_success = False
            mock_response.status_code = 503
            mock_cls.return_value.__enter__.return_value.get.return_value = (
                mock_response
            )

            with pytest.raises(EngineTransportError) as exc_info:
                fetch_json("http://example.com/flaky")

        assert exc_info.value.status_code == 503
        # Should have attempted _MAX_ATTEMPTS times
        assert mock_cls.return_value.__enter__.return_value.get.call_count == 4

    def test_network_error_retries_then_raises(self):
        with (
            patch("dalston.engine_sdk.http.httpx.Client") as mock_cls,
            patch("dalston.engine_sdk.http.time.sleep"),
        ):
            mock_cls.return_value.__enter__.return_value.get.side_effect = (
                ConnectionError("refused")
            )

            with pytest.raises(EngineTransportError, match="network error"):
                fetch_json("http://example.com/unreachable")


class TestPutJson:
    def test_success(self):
        with patch("dalston.engine_sdk.http.httpx.Client") as mock_cls:
            mock_response = MagicMock()
            mock_response.is_success = True
            mock_cls.return_value.__enter__.return_value.put.return_value = (
                mock_response
            )

            put_json("http://minio:9000/bucket/output.json?sig=x", {"result": "ok"})

        call_kwargs = mock_cls.return_value.__enter__.return_value.put.call_args
        assert json.loads(call_kwargs.kwargs["content"]) == {"result": "ok"}
        assert call_kwargs.kwargs["headers"]["Content-Type"] == "application/json"

    def test_permanent_4xx_raises_immediately(self):
        with patch("dalston.engine_sdk.http.httpx.Client") as mock_cls:
            mock_response = MagicMock()
            mock_response.is_success = False
            mock_response.status_code = 403
            mock_cls.return_value.__enter__.return_value.put.return_value = (
                mock_response
            )

            with pytest.raises(EngineTransportError) as exc_info:
                put_json("http://example.com/expired", {"data": 1})

        assert exc_info.value.status_code == 403
        assert mock_cls.return_value.__enter__.return_value.put.call_count == 1

    def test_5xx_retries_then_raises(self):
        with (
            patch("dalston.engine_sdk.http.httpx.Client") as mock_cls,
            patch("dalston.engine_sdk.http.time.sleep"),
        ):
            mock_response = MagicMock()
            mock_response.is_success = False
            mock_response.status_code = 500
            mock_cls.return_value.__enter__.return_value.put.return_value = (
                mock_response
            )

            with pytest.raises(EngineTransportError):
                put_json("http://example.com/flaky", {})

        assert mock_cls.return_value.__enter__.return_value.put.call_count == 4


# =============================================================================
# TaskInputData schema test
# =============================================================================


class TestTaskInputDataOutputUrl:
    def test_output_url_required(self):
        """output_url must be present — no backward compat fallback."""
        from pydantic import ValidationError

        from dalston.common.pipeline_types import TaskInputData

        with pytest.raises(ValidationError, match="output_url"):
            TaskInputData(task_id="t1", job_id="j1")

    def test_output_url_stored(self):
        from dalston.common.pipeline_types import TaskInputData

        tid = TaskInputData(
            task_id="t1",
            job_id="j1",
            output_url="https://minio:9000/bucket/output.json?sig=abc",
        )
        assert tid.output_url == "https://minio:9000/bucket/output.json?sig=abc"
