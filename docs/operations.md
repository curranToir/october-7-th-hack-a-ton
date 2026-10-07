# Operating the hackathon server

## AWS resources and permissions

Defaults: account `904469541651`, region `us-east-1`, stack
`company-brain-hackathon`. The CLI checks the caller account before cloud work.
Use global flags before the subcommand to override. No Athena resources are
imported or changed.

The stack creates a dedicated VPC, public subnet, route, internet gateway,
security group with no inbound rules, instance profile, Ubuntu 24.04
`t3.medium`, 30 GiB encrypted gp3 root disk, and private encrypted artifact
bucket. Public IPv4 supplies outbound connectivity without a NAT gateway.
IMDSv2 is required with hop limit 1. No SSH key or inbound port is configured.

The host gets `AmazonSSMManagedInstanceCore` and read-only access to this
bucket's `releases/*` objects. Operators need CloudFormation/IAM/EC2/S3
provisioning permissions initially. Deployments need CloudFormation read,
S3 upload, and SSM Run Command/Session Manager access. SSM command access is
privileged host access: grant it only to trusted operators and this instance.
No AWS keys are stored in application pods or Git.

## Provisioning

```sh
python3 infrastructure/deployment/scripts/manage.py --account 904469541651 --region us-east-1 provision
```

Creates a missing stack and waits for SSM, cloud-init completion, a Ready node,
and Traefik. For an existing stack, it only verifies readiness, preserving its
AMI and infrastructure. Outputs are saved to ignored `.deployment/infrastructure.json`.

Bootstrap installs AWS CLI 2.35.6 and K3s `v1.36.5+k3s1`. The K3s binary and
installer are verified against committed SHA256 hashes. K3s includes containerd,
DNS, Traefik, service load balancing, and metrics server. The initial AMI resolves
from Canonical's Ubuntu 24.04 public SSM parameter; EC2/CloudFormation records
the resolved image. OS packages and SSM Agent are not frozen.

Container bases use pinned digests; npm uses `package-lock.json`; Python uses
`uv.lock` and hash-locked `requirements.lock`. To update Python locks intentionally:

```sh
.tools-venv/bin/uv lock
.tools-venv/bin/uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file requirements.lock
```

K3s upgrades require updating the version and both hashes and testing the upgrade.
The provision command deliberately does not update existing stacks. Review and
apply a CloudFormation change set for infrastructure changes. Instance replacement
loses local cluster state; redeploy from release artifacts after rebuilding.

## Releases

```sh
python3 infrastructure/deployment/scripts/manage.py build
python3 infrastructure/deployment/scripts/manage.py deploy
```

Build captures tracked and unignored source into a temporary snapshot, including
uncommitted changes. A source-content hash supplements the commit SHA, so dirty
source is distinguishable. Gitignored environments, credentials, build outputs,
and artifacts are excluded. Source symlinks are rejected. Never track secrets.

Docker Buildx produces three `linux/amd64` images locally. The archive contains
`images.tar`, rendered `manifests.json`, `release.json`, and `checksums.json`.
Its filename combines source commit SHA and archive SHA256 prefixes. Source
content hashes and image references are recorded inside the release.

Deploy uploads to private S3 and runs the deployment runner through SSM. The
runner verifies the outer archive and every payload checksum, rejects unexpected
filenames/symlinks/path traversal, and locks out concurrent deployments. It imports
images into containerd before applying manifests, then waits for all Deployments
and tests web/API through ingress. Failure returns nonzero with diagnostics and
preserves the successful-release pointer.

```sh
python3 infrastructure/deployment/scripts/manage.py deploy --archive .deployment/releases/RELEASE_ID.tar.gz
```

The server retains artifacts for the current and previous successful release.
Failed artifacts remain until the next success. S3 retains release objects and
versions until explicit cleanup. Kubelet cleans unused image layers; rollback
reimports its retained archive. Deployment requires at least 3 GiB free disk.

## Access and checks

```sh
python3 infrastructure/deployment/scripts/manage.py tunnel
# Open http://localhost:8080 while the command runs.
python3 infrastructure/deployment/scripts/manage.py verify
python3 infrastructure/deployment/scripts/manage.py verify --agent-template
python3 infrastructure/deployment/scripts/manage.py status
```

The tunnel requires the local Session Manager plugin. Use `tunnel --port 8081`
if needed. AWS IAM/SSM is the initial access boundary; there is no app login or
public hostname. SSM encrypts the transport; localhost/node traffic uses HTTP.

Verify checks node/Deployment health, the web empty state, API probes, internal
orchestrator connectivity, absence of application Kubernetes privileges, usage,
disk, and release state. `--agent-template` creates a temporary fourth pod,
probes its Service from the orchestrator, and removes its Deployment, Service,
and policy using an exit trap. It does not define a real agent.

## Rollback and recovery

```sh
# Restore the recorded successful version after a bad/interrupted rollout:
python3 infrastructure/deployment/scripts/manage.py rollback
# Switch to the previous successful version:
python3 infrastructure/deployment/scripts/manage.py rollback --previous
```

Rollback verifies cached payloads, reimports images, applies manifests, and
checks readiness/ingress. No prior successful deployment means no rollback is
available; fix and redeploy. K3s/SSM restart automatically after reboot. Cluster
state/images survive reboot on the root volume, but not instance replacement.

Bootstrap logs: `/var/log/cloud-init-output.log`,
`/var/log/company-brain-bootstrap.log`, and `journalctl -u k3s` through SSM.
Deployment commands print an SSM command ID for diagnosis; SSM Agent also keeps
execution output on the host. `status` prints pods, node/pod usage, disk, and
recorded release state.

Acceptance includes a bad-image rollout followed by rollback, plus a reboot
followed by verify. These interrupt the demo briefly; run them before presenting.
Measure memory/CPU before adding agents, especially browser workers/local models.

## End-of-hackathon cleanup

Running resources incur EC2, EBS, IPv4, and S3 charges. T3 Standard avoids surplus
credit charges but may throttle sustained CPU use.

When finished, delete only the `company-brain-hackathon` CloudFormation stack in
the selected account/region. This deletes the server/root disk and local cluster
state. The artifact bucket has `Retain` policies: review and delete its objects
and versions separately before deleting the bucket. Scripts never destroy
resources automatically.
