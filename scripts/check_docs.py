#!/usr/bin/env python3
"""Check user-facing documentation against generated/runtime surfaces."""

from __future__ import annotations

import re
import shlex
import sys
from collections.abc import Iterable
from pathlib import Path

from typer.main import get_command

ROOT = Path(__file__).resolve().parents[1]

TOP_LEVEL_DOCS = [
    ROOT / "README.md",
    ROOT / "cli" / "README.md",
    ROOT / "sdk" / "README.md",
    ROOT / "web" / "README.md",
    ROOT / "docs" / "README.md",
    ROOT / "docs" / "GLOSSARY.md",
]

EXCLUDED_SPEC_NAMES = {
    "DATA_RETENTION_V2_DRAFT.md",
    "PARITY_GAPS.md",
}

WEBSOCKET_PATHS = {
    "/v1/audio/transcriptions/stream",
    "/v1/speech-to-text/realtime",
    "/v1/realtime",
}

NON_OPENAPI_PATHS = {
    "/v1",
    "/openapi.json",
    "/metrics",
}

OBSOLETE_COMPOSE_SERVICES = {
    "session-router",
    "stt-merge",
    "stt-transcribe-whisper-cpu",
    "stt-diarize-pyannote-v40-cpu",
    "llm-cleanup",
}


def active_docs() -> list[Path]:
    docs = list(TOP_LEVEL_DOCS)
    docs.extend(sorted((ROOT / "docs" / "guides").glob("*.md")))
    docs.extend(sorted((ROOT / "docs" / "reference").glob("*.md")))
    docs.extend(sorted((ROOT / "docs" / "specs").glob("*.md")))
    docs.extend(sorted((ROOT / "docs" / "specs" / "batch").glob("*.md")))
    docs.extend(sorted((ROOT / "docs" / "specs" / "openai").glob("*.md")))
    docs.extend(sorted((ROOT / "docs" / "specs" / "realtime").glob("*.md")))
    docs.extend(sorted((ROOT / "docs" / "specs" / "examples").glob("*.md")))
    active: list[Path] = []
    for path in docs:
        if path.name in EXCLUDED_SPEC_NAMES or "implementations" in path.parts:
            continue
        opening = path.read_text()[:500]
        if "**Status:** Draft" in opening or "**Audience:** Internal" in opening:
            continue
        active.append(path)
    return active


def relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def strip_fenced_blocks(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.DOTALL)


def check_links(files: Iterable[Path]) -> list[str]:
    errors: list[str] = []
    link_re = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
    image_re = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")

    for path in files:
        text = path.read_text()
        for match in [*link_re.finditer(text), *image_re.finditer(text)]:
            raw = match.group(1).strip()
            if raw.startswith("<") and raw.endswith(">"):
                raw = raw[1:-1]
            target = raw.split("#", 1)[0]
            if (
                not target
                or "://" in target
                or target.startswith(("mailto:", "/", "data:"))
            ):
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                line = text.count("\n", 0, match.start()) + 1
                errors.append(f"{relative(path)}:{line}: broken local link {raw!r}")
    return errors


def normalize_api_path(raw: str) -> str:
    path = raw.rstrip(".,;:)]'\"")
    path = path.split("?", 1)[0]
    path = re.sub(r"\{[^}]+\}", "{}", path)
    return path


def check_api_paths(files: Iterable[Path]) -> list[str]:
    from dalston.gateway.main import app

    openapi_paths = {normalize_api_path(path) for path in app.openapi()["paths"].keys()}
    engine_http_paths = {
        "/v1/capabilities",
        "/v1/transcribe",
        "/v1/diarize",
    }
    valid = openapi_paths | WEBSOCKET_PATHS | NON_OPENAPI_PATHS | engine_http_paths

    def matches_template(candidate: str) -> bool:
        candidate_parts = candidate.strip("/").split("/")
        for template in valid:
            template_parts = template.strip("/").split("/")
            if len(candidate_parts) != len(template_parts):
                continue
            if all(
                expected == "{}" or expected == actual
                for actual, expected in zip(
                    candidate_parts, template_parts, strict=True
                )
            ):
                return True
        return False

    path_re = re.compile(r"/v1(?:/[A-Za-z0-9_.{}*-]+)+")
    errors: list[str] = []

    for path in files:
        text = path.read_text()
        for match in path_re.finditer(text):
            raw = match.group(0)
            if "*" in raw or "..." in raw or raw.endswith(".py"):
                continue
            normalized = normalize_api_path(raw)
            if not matches_template(normalized):
                line = text.count("\n", 0, match.start()) + 1
                errors.append(
                    f"{relative(path)}:{line}: undocumented runtime path {raw}"
                )
    return errors


def _option_names(command: object) -> set[str]:
    # typer >= 0.26 vendors click (typer._click), so typer-built parameters
    # are not instances of the installed click's Option; duck-type instead.
    return {
        option
        for parameter in getattr(command, "params", [])
        if getattr(parameter, "param_type_name", None) == "option"
        for option in (*parameter.opts, *parameter.secondary_opts)
    }


def cli_tree() -> tuple[
    set[str],
    dict[str, set[str]],
    dict[tuple[str, ...], set[str]],
]:
    from dalston_cli.main import app

    root = get_command(app)
    root_commands = getattr(root, "commands", None)
    assert root_commands is not None, "CLI root is not a command group"
    roots = set(root_commands)
    groups: dict[str, set[str]] = {}
    options: dict[tuple[str, ...], set[str]] = {}
    for name, command in root_commands.items():
        options[(name,)] = _option_names(command)
        subcommands = getattr(command, "commands", None)
        if subcommands is not None:
            groups[name] = set(subcommands)
            for child_name, child in subcommands.items():
                options[(name, child_name)] = _option_names(child)
    return roots, groups, options


def check_cli_commands(files: Iterable[Path]) -> list[str]:
    roots, groups, options = cli_tree()
    command_re = re.compile(
        r"(?m)(?:^[ \t]*(?:[A-Z_][A-Z0-9_]*=[^ \t]+[ \t]+)*|\$\()"
        r"dalston[ \t]+([^\n`]+)"
    )
    errors: list[str] = []

    for path in files:
        text = path.read_text()
        for match in command_re.finditer(text):
            command = match.group(1).split("#", 1)[0].strip()
            try:
                tokens = shlex.split(command)
            except ValueError:
                continue
            if not tokens or tokens[0].startswith(("-", "$", "<", "(")):
                continue
            root_name = tokens[0]
            if root_name not in roots:
                line = text.count("\n", 0, match.start()) + 1
                errors.append(
                    f"{relative(path)}:{line}: unknown CLI command "
                    f"'dalston {root_name}'"
                )
                continue
            if root_name in groups and len(tokens) > 1:
                child = tokens[1]
                if (
                    not child.startswith(("-", "$", "<", "[", "{", "("))
                    and child not in groups[root_name]
                ):
                    line = text.count("\n", 0, match.start()) + 1
                    errors.append(
                        f"{relative(path)}:{line}: unknown CLI command "
                        f"'dalston {root_name} {child}'"
                    )
                    continue

            command_path = (root_name,)
            if root_name in groups and len(tokens) > 1:
                child = tokens[1].strip("[]{}()")
                if child in groups[root_name]:
                    command_path = (root_name, child)
            allowed_options = options.get(command_path, set()) | {"--help"}
            command_tokens = tokens
            for separator in ("|", "&&", ";", ">", ">>"):
                if separator in command_tokens:
                    command_tokens = command_tokens[: command_tokens.index(separator)]
            for token in command_tokens[len(command_path) :]:
                option = token.strip("[]{}(),").split("=", 1)[0]
                if option.startswith("-") and option not in allowed_options:
                    line = text.count("\n", 0, match.start()) + 1
                    errors.append(
                        f"{relative(path)}:{line}: option {option!r} is not "
                        f"in 'dalston {' '.join(command_path)} --help'"
                    )
    return errors


def check_obsolete_services(files: Iterable[Path]) -> list[str]:
    errors: list[str] = []
    for path in files:
        text = path.read_text()
        for number, line in enumerate(text.splitlines(), start=1):
            if "docker compose" not in line and "docker-compose" not in line:
                continue
            for service in OBSOLETE_COMPOSE_SERVICES:
                if re.search(rf"\b{re.escape(service)}\b", line):
                    errors.append(
                        f"{relative(path)}:{number}: obsolete Compose service "
                        f"{service!r}"
                    )
    return errors


def check_performance_metadata(files: Iterable[Path]) -> list[str]:
    errors: list[str] = []
    numeric_rtf = re.compile(
        r"(?:\bRTF\s*(?:=|x|of|:)?\s*~?\d|\d+(?:\.\d+)?\s*RTF\b)",
        re.IGNORECASE,
    )
    marker = re.compile(r"<!--\s*(?:benchmark-context|performance-data):")

    for path in files:
        if path.parent != ROOT / "docs" / "guides":
            continue
        text = path.read_text()
        if numeric_rtf.search(text) and not marker.search(text):
            errors.append(
                f"{relative(path)}: numeric RTF data needs benchmark context "
                "or an explicit estimate marker"
            )
    return errors


def check_cloud_pricing(files: Iterable[Path]) -> list[str]:
    errors: list[str] = []
    allowed = ROOT / "docs" / "guides" / "51-aws-cost-estimator.md"
    price_rate = re.compile(
        r"(?:[$£€]\s*~?\d[\d.,]*(?:\s*[–-]\s*\d[\d.,]*)?"
        r"\s*/\s*(?:h|hr|hour|mo|month)|"
        r"[$£€]\s*~?\d[\d.,]*\s+per\s+(?:hour|month))",
        re.IGNORECASE,
    )

    for path in files:
        if path == allowed:
            continue
        text = strip_fenced_blocks(path.read_text())
        match = price_rate.search(text)
        if match:
            line = text.count("\n", 0, match.start()) + 1
            errors.append(
                f"{relative(path)}:{line}: volatile rate belongs only in "
                "docs/guides/51-aws-cost-estimator.md"
            )

    pricing = allowed.read_text()
    if "pricing-snapshot:" not in pricing:
        errors.append(
            "docs/guides/51-aws-cost-estimator.md: missing pricing-snapshot date"
        )
    return errors


def main() -> int:
    files = active_docs()
    checks = [
        ("links", check_links),
        ("OpenAPI paths", check_api_paths),
        ("CLI commands", check_cli_commands),
        ("Compose services", check_obsolete_services),
        ("performance metadata", check_performance_metadata),
        ("cloud pricing", check_cloud_pricing),
    ]
    failures: list[str] = []

    for name, check in checks:
        errors = check(files)
        if errors:
            failures.extend(f"[{name}] {error}" for error in errors)
        else:
            print(f"docs: {name}: ok")

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
