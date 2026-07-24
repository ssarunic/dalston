# Deploying Dalston on AWS

`infra/scripts/dalston-aws` provisions and operates the AWS deployment used by
this repository. It is a Python 3.11+ tool backed by boto3 and YAML templates;
it does not use Terraform.

Run it from the repository virtual environment when the system Python is
older:

```bash
.venv/bin/python infra/scripts/dalston-aws --help
```

The examples below assume `dalston-aws` resolves to that command.

## Prerequisites

1. Configure AWS credentials and verify them:

   ```bash
   aws sts get-caller-identity
   ```

2. Install the project’s Python dependencies, including boto3 and PyYAML.

3. Join the operator machine to Tailscale and enable MagicDNS.

4. Store a reusable Tailscale auth key in the deployment region:

   ```bash
   aws ssm put-parameter \
     --name /dalston/tailscale-auth-key \
     --type SecureString \
     --overwrite \
     --value tskey-auth-...
   ```

   `/dalston/tailscale-api-key` is optional. When present, bootstrap can remove
   an offline node with the same hostname before joining.

5. For tailnet HTTPS, enable HTTPS certificates in the Tailscale DNS settings.

6. Export `HF_TOKEN` before `launch` when a selected worker includes
   `pyannote`.

Do not `source .env.example` before running this tool: its MinIO credentials
would override real AWS credentials in boto3.

## Two separate phases

`setup` provisions shared infrastructure only. `launch` creates instances.
This distinction is intentional.

```bash
dalston-aws setup -t split
dalston-aws launch control-plane
dalston-aws launch gpu --engines nemo,pyannote
```

`setup` requires `-t/--template`; flags such as `--cpu`, `--gpu`,
`--instance-type`, and `--spot` are not setup options.

## Templates

The source of truth is `infra/templates/`.

| Template | Current shape |
| --- | --- |
| `cpu` | One `t3.xlarge` control-plane host, no GPU worker definition |
| `gpu` | One `g4dn.xlarge` host using the local-infra and GPU profiles |
| `split` | `t3.large` control plane plus a `g6.xlarge` spot GPU worker |

Instance types, regions, profiles, and spot defaults can be changed in a
custom template:

```bash
dalston-aws setup -t path/to/template.yaml
```

The repository does not contain a Terraform, ECS, or autoscaling deployment.
Those require separate infrastructure work; they are not modes of
`dalston-aws`.

## Recommended split deployment

Launch the control plane first, then choose only the GPU engines you intend to
co-locate:

```bash
dalston-aws setup -t split
dalston-aws launch control-plane --observability

export HF_TOKEN=hf_...  # only when pyannote is selected
dalston-aws launch gpu \
  --engines nemo,pyannote \
  --spot
```

`launch gpu` supports:

- `--engines` with comma-separated preset keys;
- `--gpu-type` to override the template instance type;
- `--spot` or `--on-demand`;
- `--region` for a worker in another region.

Available preset keys are `onnx`, `faster-whisper`, `nemo`, `pyannote`,
`vllm-asr`, and `hf-asr`. The script checks declared compute-capability
requirements, but the operator still owns aggregate VRAM sizing when
co-locating engines.

Omitting `--engines` selects every preset known to the script. That is useful
for automation but is not a promise that all models fit simultaneously on the
template GPU.

## What gets provisioned

Shared setup creates or records:

- an S3 artifact bucket;
- an IAM role and instance profile;
- an EC2 key pair, saving a newly created private key under `~/.dalston/`;
- control-plane and, for split mode, GPU security groups; and
- local deployment state.

The control plane uses a persistent EBS data volume for PostgreSQL, Redis,
models, and operational data. Replacement control-plane instances reattach
that volume in its availability zone. GPU workers use replaceable local model
storage and shared S3 artifacts.

Local state is YAML:

```text
~/.dalston/aws-state.yaml
~/.dalston/engine-state.yaml
~/.dalston/audit.log
```

Deleting a state file does not delete AWS resources; it only removes the
tool’s record of them.

## Access and first request

The control plane joins as `dalston-control-plane`. Bootstrap configures
Tailscale Serve from HTTPS port 443 to the gateway on localhost port 8000.
Use the full tailnet DNS name in browsers:

```text
https://dalston-control-plane.<tailnet>.ts.net/
```

`setup` generates an admin API key in `aws-state.yaml`; `launch` seeds it into
the gateway:

```bash
API_KEY=$(awk -F': ' '/^api_key:/ {print $2}' ~/.dalston/aws-state.yaml)
URL=https://dalston-control-plane.<tailnet>.ts.net

curl -X POST "$URL/v1/audio/transcriptions" \
  -H "Authorization: Bearer $API_KEY" \
  -F file=@meeting.wav
```

If HTTPS was enabled after launch:

```bash
dalston-aws ssh
sudo systemctl restart dalston-tailscale-serve
tailscale serve status
```

## Day-to-day operations

```bash
dalston-aws status
dalston-aws ssh
dalston-aws ssh gpu
dalston-aws ssh gpu --name nemo-pyannote
dalston-aws reconcile
dalston-aws patch
```

Multiple GPU workers with the same engine label are distinguished by their
resolved Tailscale hostnames. Use `--name` to choose one.

Lifecycle commands have different consequences:

| Command | Effect |
| --- | --- |
| `down` | Stops stoppable instances; one-time spot instances may be terminated |
| `up` | Starts retained instances; terminated workers must be launched again |
| `terminate gpu --name NAME` | Terminates selected GPU worker replicas |
| `terminate control-plane` | Terminates the host and deletes its recorded data volume |
| `teardown` | Removes tracked deployment resources; preserves the S3 bucket |

After a spot worker is reclaimed or terminated:

```bash
dalston-aws launch gpu --engines nemo,pyannote --spot
```

Do not assume `up` can restart a terminated one-time spot worker.

## Logs and updates

Control-plane bootstrap and service logs:

```bash
dalston-aws ssh
sudo tail -f /var/log/user-data.log
sudo systemctl status dalston
cd /data/dalston
sudo /data/dalston/deploy.sh
```

The generated deploy script pulls the repository and GHCR images using the
Compose file list stored in `.env.aws`. From the operator machine,
`dalston-aws up --pull` starts retained instances and runs that deployment
script after SSH becomes reachable.

GPU worker diagnostics:

```bash
dalston-aws ssh gpu --name nemo-pyannote
sudo systemctl status dalston-gpu
sudo journalctl -u dalston-gpu -n 100
docker ps
nvidia-smi
```

## Troubleshooting

### Tailscale hostname does not resolve

Confirm the SSM auth-key parameter exists in the same region, inspect
`dalston-tailscale.service`, then run:

```bash
dalston-aws reconcile
```

### Gateway returns 502 through Tailscale Serve

Tailscale Serve can be ready before the gateway. On the control plane:

```bash
curl http://127.0.0.1:8000/health
sudo systemctl status dalston
docker ps
```

### GPU worker is running but no engine registers

Check `dalston-gpu.service`, the engine container logs, Redis reachability to
`dalston-control-plane:6379`, `HF_TOKEN`, and the selected GPU’s compatibility.

### Need current prices

Instance, EBS, S3, and spot prices vary by region and time. Use the AWS Pricing
Calculator and current EC2 spot history rather than the historical dollar
figures that used to appear in this guide.

## See also

- [AWS deployment scenarios](aws-deployment-scenarios.md)
- [Single-engine Tailscale mode](11-single-engine-tailscale-mode.md)
- [Control-plane walkthrough](21-control-plane-aws-deploy.md)
- [Cost correlation](aws-cost-correlation.md)
