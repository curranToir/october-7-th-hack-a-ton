"""Operator-only infrastructure updates and secret input. No secret values in CLI arguments."""

import getpass
import json
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from aws_ops import Aws

SECRET_FIELDS = {
    "respan": ("RESPAN_API_KEY",),
    "scalekit": (
        "SCALEKIT_ENVIRONMENT_URL",
        "SCALEKIT_CLIENT_ID",
        "SCALEKIT_CLIENT_SECRET",
        "SCALEKIT_CONNECTION_NAME",
        "SCALEKIT_ACCOUNT_ID",
    ),
    "exa": ("EXA_API_KEY",),
}

EXISTING_SECRET_PARAMETERS = {
    "DatabaseSecretArn": "database",
    "BrainApiSecretArn": "brain-api",
}


def existing_secret_parameters(aws: Aws) -> list[dict]:
    """Resolve metadata only; never recreate Spark-owned secrets or read their values."""
    parameters = []
    for parameter, suffix in EXISTING_SECRET_PARAMETERS.items():
        name = f"/{aws.stack}/{suffix}"
        try:
            metadata = aws.json("secretsmanager", "describe-secret", "--secret-id", name)
        except subprocess.CalledProcessError:
            raise RuntimeError(
                f"Cannot resolve existing {suffix} secret; ask its owner to provision it"
            ) from None
        arn = metadata.get("ARN", "")
        if not re.fullmatch(r"arn:[a-z-]+:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:[^*?]+", arn):
            raise RuntimeError(f"Invalid ARN metadata for existing {suffix} secret")
        parameters.append({"ParameterKey": parameter, "ParameterValue": arn})
    return parameters


def cloudformation_yaml(text: str) -> dict:
    import yaml

    class Loader(yaml.SafeLoader):
        pass

    def intrinsic(loader, suffix, node):
        key = suffix if suffix in {"Ref", "Condition"} else "Fn::" + suffix
        if isinstance(node, yaml.ScalarNode):
            value = loader.construct_scalar(node)
            if suffix == "GetAtt":
                value = value.split(".", 1)
        elif isinstance(node, yaml.SequenceNode):
            value = loader.construct_sequence(node)
        else:
            value = loader.construct_mapping(node)
        return {key: value}

    Loader.add_multi_constructor("!", intrinsic)
    return yaml.load(text, Loader=Loader)


def protected_changes(current: dict, candidate: dict, changes: list[dict]) -> list[str]:
    # Bucket lifecycle updates may propagate a dependency-only bucket-policy change.
    # Permit that notification only when the complete policy resource is unchanged.
    permitted = {"HostRole", "Artifacts", "RespanSecret", "ScalekitSecret", "ExaSecret"}
    policy_unchanged = current["Resources"].get("ArtifactPolicy") is not None and current[
        "Resources"
    ]["ArtifactPolicy"] == candidate["Resources"].get("ArtifactPolicy")
    unsafe = []
    for item in changes:
        resource = item["ResourceChange"]
        name = resource["LogicalResourceId"]
        dependency_only = (
            name == "ArtifactPolicy" and policy_unchanged and resource.get("Action") == "Modify"
        )
        if (
            (name not in permitted and not dependency_only)
            or resource.get("Replacement") in {"True", "Conditional"}
            or resource.get("Action") == "Remove"
        ):
            unsafe.append(name)
    return unsafe


def stack_update(aws: Aws, template: Path):
    """Preserve the running host and refuse any change set that could replace it."""
    current = aws.json("cloudformation", "get-template", "--stack-name", aws.stack)["TemplateBody"]
    if isinstance(current, str):
        current = cloudformation_yaml(current)
    candidate = cloudformation_yaml(template.read_text())
    candidate["Resources"]["Host"] = current["Resources"]["Host"]
    outputs = aws.outputs()
    host = aws.json("ec2", "describe-instances", "--instance-ids", outputs["InstanceId"])
    image = host["Reservations"][0]["Instances"][0]["ImageId"]
    # An SSM AMI parameter re-resolves during updates, even with UsePreviousValue.
    # Pin it to the actual running AMI for this existing stack update.
    candidate["Parameters"]["UbuntuAmi"] = {"Type": "String", "Default": image}
    parameters = [
        {"ParameterKey": name, "UsePreviousValue": True}
        for name in candidate.get("Parameters", {})
        if name != "UbuntuAmi"
        and name not in EXISTING_SECRET_PARAMETERS
        and name in current.get("Parameters", {})
    ]
    parameters.append({"ParameterKey": "UbuntuAmi", "ParameterValue": image})
    parameters.extend(existing_secret_parameters(aws))
    change_set = "toir-runtime-" + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix="toir-stack-") as tmp:
        prepared = Path(tmp) / "stack.json"
        prepared.write_text(json.dumps(candidate))
        aws.call(
            "cloudformation",
            "create-change-set",
            "--stack-name",
            aws.stack,
            "--change-set-name",
            change_set,
            "--change-set-type",
            "UPDATE",
            "--template-body",
            f"file://{prepared}",
            "--capabilities",
            "CAPABILITY_IAM",
            "--parameters",
            json.dumps(parameters),
        )
    try:
        aws.call(
            "cloudformation",
            "wait",
            "change-set-create-complete",
            "--stack-name",
            aws.stack,
            "--change-set-name",
            change_set,
        )
    except subprocess.CalledProcessError:
        change = aws.json(
            "cloudformation",
            "describe-change-set",
            "--stack-name",
            aws.stack,
            "--change-set-name",
            change_set,
        )
        if "didn't contain changes" in change.get("StatusReason", ""):
            aws.call(
                "cloudformation",
                "delete-change-set",
                "--stack-name",
                aws.stack,
                "--change-set-name",
                change_set,
            )
            print("Stack already matches the runtime infrastructure.")
            return
        raise RuntimeError("CloudFormation could not create the runtime change set") from None
    change = aws.json(
        "cloudformation",
        "describe-change-set",
        "--stack-name",
        aws.stack,
        "--change-set-name",
        change_set,
    )
    unsafe = protected_changes(current, candidate, change.get("Changes", []))
    if unsafe:
        aws.call(
            "cloudformation",
            "delete-change-set",
            "--stack-name",
            aws.stack,
            "--change-set-name",
            change_set,
        )
        raise RuntimeError(
            "Refused infrastructure update that could change protected resources: "
            + ", ".join(unsafe)
        )
    print("Updating runtime IAM, retained secrets and backup lifecycle; EC2 is unchanged.")
    aws.call(
        "cloudformation",
        "execute-change-set",
        "--stack-name",
        aws.stack,
        "--change-set-name",
        change_set,
    )
    aws.call("cloudformation", "wait", "stack-update-complete", "--stack-name", aws.stack)
    if aws.outputs()["InstanceId"] != outputs["InstanceId"]:
        raise RuntimeError("Unexpected instance identity after infrastructure update")
    print("Runtime infrastructure updated.")


def set_secret(aws: Aws, name: str, from_stdin: bool):
    """Read directly from a hidden terminal prompt or stdin; never create a secret file."""
    import boto3

    fields = SECRET_FIELDS[name]
    if from_stdin:
        try:
            values = json.load(sys.stdin)
        except ValueError:
            raise ValueError("Expected a JSON object on stdin; contents were not logged") from None
    else:
        values = {field: getpass.getpass(f"{field} (hidden): ") for field in fields}
    if (
        not isinstance(values, dict)
        or set(values) != set(fields)
        or any(not isinstance(v, str) or not v.strip() for v in values.values())
    ):
        raise ValueError("Supply every required field as a nonempty string: " + ", ".join(fields))
    client = boto3.client("secretsmanager", region_name=aws.region)
    try:
        client.put_secret_value(SecretId=f"/{aws.stack}/{name}", SecretString=json.dumps(values))
    except Exception:
        raise RuntimeError(
            "Secret storage failed; check operator permissions and run stack-update"
        ) from None
    print(
        f"Stored {name} in AWS Secrets Manager. "
        "Run secret-sync --restart to activate runtime changes."
    )
