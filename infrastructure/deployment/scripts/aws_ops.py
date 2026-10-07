"""Small AWS CLI adapter; credentials stay in the normal AWS credential chain."""

import json
import subprocess
import time


class Aws:
    def __init__(self, account: str, region: str, stack: str):
        self.account = account
        self.region = region
        self.stack = stack

    def call(self, *args: str, capture: bool = True):
        command = ["aws", "--region", self.region, "--no-cli-pager", *args]
        result = subprocess.run(command, check=True, text=True, capture_output=capture)
        return result.stdout if capture else None

    def json(self, *args: str):
        return json.loads(self.call(*args, "--output", "json"))

    def verify_account(self):
        actual = self.json("sts", "get-caller-identity")["Account"]
        if actual != self.account:
            raise RuntimeError(f"Expected AWS account {self.account}; authenticated as {actual}.")
        print(f"AWS account {actual}, region {self.region}, stack {self.stack}", flush=True)

    def outputs(self) -> dict[str, str]:
        stack = self.json("cloudformation", "describe-stacks", "--stack-name", self.stack)
        return {o["OutputKey"]: o["OutputValue"] for o in stack["Stacks"][0]["Outputs"]}

    def wait_online(self, instance: str, timeout: int = 900):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self.json(
                "ssm", "describe-instance-information",
                "--filters", f"Key=InstanceIds,Values={instance}",
            )["InstanceInformationList"]
            if result and result[0]["PingStatus"] == "Online":
                return
            time.sleep(10)
        raise RuntimeError(f"Instance {instance} did not connect to SSM in {timeout} seconds.")

    def run_ssm(self, instance: str, script: str, timeout: int = 900) -> dict:
        command = self.json(
            "ssm", "send-command", "--instance-ids", instance,
            "--document-name", "AWS-RunShellScript",
            "--parameters", json.dumps({"commands": [script], "executionTimeout": [str(timeout)]}),
            "--timeout-seconds", "600", "--comment", "Company Brain deployment tooling",
        )["Command"]["CommandId"]
        print(f"SSM command {command}", flush=True)
        deadline = time.monotonic() + timeout + 660
        next_progress = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                result = self.json(
                    "ssm", "get-command-invocation", "--command-id", command,
                    "--instance-id", instance,
                )
            except subprocess.CalledProcessError as error:
                if "InvocationDoesNotExist" not in (error.stderr or ""):
                    raise
                time.sleep(2)
                continue
            status = result["Status"]
            if status not in {"Pending", "InProgress", "Delayed", "Cancelling"}:
                print(result.get("StandardOutputContent", ""), end="", flush=True)
                print(result.get("StandardErrorContent", ""), end="", flush=True)
                if status != "Success" or result.get("ResponseCode") != 0:
                    raise RuntimeError(f"SSM command {command}: {status}")
                return result
            if time.monotonic() >= next_progress:
                print(f"SSM {command}: {status}", flush=True)
                next_progress = time.monotonic() + 30
            time.sleep(3)
        raise RuntimeError(f"SSM command {command} exceeded the local wait deadline; inspect AWS.")
