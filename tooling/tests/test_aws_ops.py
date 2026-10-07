import pytest
from aws_ops import Aws


def test_account_mismatch_stops_cloud_work(monkeypatch):
    aws = Aws("123456789012", "us-east-1", "test")
    monkeypatch.setattr(aws, "json", lambda *args: {"Account": "999999999999"})
    with pytest.raises(RuntimeError, match="Expected AWS account"):
        aws.verify_account()


def test_ssm_failure_is_not_reported_as_success(monkeypatch):
    aws = Aws("123456789012", "us-east-1", "test")
    answers = iter([
        {"Command": {"CommandId": "test-command"}},
        {"Status": "Failed", "ResponseCode": 1, "StandardErrorContent": "bad release"},
    ])
    monkeypatch.setattr(aws, "json", lambda *args: next(answers))
    with pytest.raises(RuntimeError, match="test-command: Failed"):
        aws.run_ssm("instance", "false")
