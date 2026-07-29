"""Tests for M94 nightly Postgres backups in dalston-aws.

The dalston-aws script lives outside the python package as an executable
without a ``.py`` suffix. Tests import it via ``importlib.util`` and exercise
the backup provisioning script, the lifecycle-rule set, the user-data
assembly, and the ``backup`` subcommand wiring — everything that runs
without AWS credentials.
"""

from __future__ import annotations

import importlib.util
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "infra" / "scripts" / "dalston-aws"


def _load_dalston_aws():
    """Load infra/scripts/dalston-aws as a module without invoking ``main()``."""
    loader = SourceFileLoader("dalston_aws_backup_under_test", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader, f"could not load {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def aws():
    return _load_dalston_aws()


class TestLifecycleRules:
    def test_all_three_rules_present(self, aws):
        rules = {r["ID"]: r for r in aws.S3_LIFECYCLE_RULES}
        assert set(rules) == {"cleanup-temp", "cleanup-jobs", "cleanup-backups"}

    def test_backup_rule_prefix_covers_dump_keys(self, aws):
        backups = next(
            r for r in aws.S3_LIFECYCLE_RULES if r["ID"] == "cleanup-backups"
        )
        assert aws.BACKUP_S3_PREFIX.startswith(backups["Filter"]["Prefix"])
        assert backups["Expiration"]["Days"] == aws.BACKUP_RETENTION_DAYS
        assert backups["Status"] == "Enabled"


class TestProvisionScript:
    def test_installs_timer_and_service(self, aws):
        script = aws._backup_provision_script()
        assert "dalston-backup.service" in script
        assert "dalston-backup.timer" in script
        assert f"OnCalendar={aws.BACKUP_TIMER_ONCALENDAR}" in script
        assert "Persistent=true" in script
        assert "systemctl enable --now dalston-backup.timer" in script

    def test_retries_transient_failures(self, aws):
        script = aws._backup_provision_script()
        assert "Restart=on-failure" in script
        assert "RestartSec=" in script
        # Without a start timeout a hung dump blocks retries and every
        # later timer activation (oneshot has none by default)
        assert "TimeoutStartSec=" in script

    def test_refuses_stale_checkout(self, aws):
        # A pre-M94 clone has no `backup` subcommand; the script must
        # verify before enabling a timer that would fail every night.
        script = aws._backup_provision_script()
        assert "git pull --ff-only" in script
        assert "backup --help" in script
        assert "exit 1" in script

    def test_marks_checkout_safe_before_pull(self, aws):
        # Provisioning runs as root against a uid-1000-owned mount; git
        # rejects the pull as dubious ownership without safe.directory.
        script = aws._backup_provision_script()
        assert script.index("safe.directory /data/dalston") < script.index(
            "git pull --ff-only"
        )

    def test_no_unexpanded_placeholders(self, aws):
        script = aws._backup_provision_script()
        assert "{BACKUP" not in script and "{NAME" not in script


class TestUserData:
    def test_backup_default_on_autoscale_opt_in(self, aws, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "")
        ud = aws.generate_user_data("bucket", "eu-west-2", "--profile cpu")
        assert "dalston-backup.timer" in ud
        assert "backup --once" in ud
        assert "dalston-autoscale" not in ud

    def test_backup_block_after_repo_clone(self, aws, monkeypatch):
        # The provision block git-pulls /data/dalston, so it must come
        # after the body that clones the repo.
        monkeypatch.setenv("HF_TOKEN", "")
        ud = aws.generate_user_data("bucket", "eu-west-2", "--profile cpu")
        assert ud.index("git clone") < ud.index("dalston-backup.service")


class TestBackupCli:
    def test_parser_dispatches_to_cmd_backup(self, aws):
        parser = aws.build_parser()
        args = parser.parse_args(["backup", "--once"])
        assert args.func is aws.cmd_backup
        assert args.once and not args.provision

    def test_no_flags_dies(self, aws):
        parser = aws.build_parser()
        args = parser.parse_args(["backup"])
        with pytest.raises(SystemExit):
            aws.cmd_backup(args)

    def test_once_requires_env(self, aws, monkeypatch):
        monkeypatch.delenv("DALSTON_S3_BUCKET", raising=False)
        monkeypatch.delenv("AWS_REGION", raising=False)
        with pytest.raises(SystemExit):
            aws._backup_once()
