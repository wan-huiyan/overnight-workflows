#!/usr/bin/env python3
"""Structural validation for a Claude Code plugin marketplace repo.

Checks (stdlib only, no external deps):
  1. .claude-plugin/marketplace.json parses and has name / owner / plugins[].
  2. Every marketplace plugin `source` dir exists and is registered exactly once.
  3. Every plugin dir on disk is registered in marketplace.json (no orphans).
  4. Every plugin has a .claude-plugin/plugin.json that parses, with name == dir basename,
     and that name matches the marketplace entry.
  5. Every plugin exposes a skill: either plugins/<name>/SKILL.md, or a nested
     plugins/<name>/skills/<skill>/SKILL.md set (multi-skill plugin).
  6. Every SKILL.md frontmatter `name:` equals its containing directory name.
  7. Marketplace and plugin manifest versions match.
  8. The release ledger binds every changed routed plugin payload and content
     version to its distributable identity and the fixed published base.
  9. If a VERSION file exists: it is non-empty; and for a single-plugin repo it must
     equal that plugin's plugin.json version (drift guard).

Exit 0 = all good; exit 1 = one or more failures (printed).
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
errors = []
warnings = []
RELEASE_LEDGER = os.path.join(ROOT, "scripts", "plugin_release_ledger.json")
PAYLOAD_FORMAT = "sha256-size-path-v1"
GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
INSIGHT_PLUGIN = "overnight-insight-discovery"
PUBLISHED_BASE = "3df43c37ecdf8d62343907b49b28f0fbb1bf4338"
ROUTED_PLUGINS = {
    "large-redesign-parallel-branch-collision-audit",
    INSIGHT_PLUGIN,
    "overnight-multi-issue-implementation",
    "overnight-review-client-delivery",
    "overnight-review-panel-blocked-reviewer-reads-as-clean",
    "schedule-poll-orchestrator-pattern",
    "subagent-review-tier-calibration-for-overnight-pr-chains",
}


def err(m): errors.append(m)
def warn(m): warnings.append(m)


def frontmatter_name(skill_md):
    """Return the `name:` value from a SKILL.md YAML frontmatter block, or None."""
    try:
        with open(skill_md, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        err(f"cannot read {skill_md}: {e}")
        return None
    if not text.startswith("---"):
        err(f"{skill_md}: missing YAML frontmatter")
        return None
    end = text.find("\n---", 3)
    block = text[3:end] if end != -1 else text
    m = re.search(r"^name:\s*(.+?)\s*$", block, re.MULTILINE)
    if not m:
        err(f"{skill_md}: no `name:` in frontmatter")
        return None
    return m.group(1).strip().strip('"').strip("'")


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        err(f"invalid JSON {path}: {e}")
        return None


def content_version(skill_md, require_codex_frontmatter=False):
    """Return content version while enforcing Codex frontmatter where required."""
    try:
        with open(skill_md, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        return None, f"cannot read {skill_md}: {e}"
    end = text.find("\n---", 3)
    if not text.startswith("---") or end == -1:
        return None, f"{skill_md}: missing closed YAML frontmatter"
    frontmatter_keys = {
        match.group(1)
        for match in re.finditer(r"^([A-Za-z0-9_-]+):", text[3:end], re.MULTILINE)
    }
    if require_codex_frontmatter and frontmatter_keys != {"name", "description"}:
        return None, (
            f"{skill_md}: frontmatter must contain only name and description; "
            f"found {sorted(frontmatter_keys)}"
        )
    if require_codex_frontmatter:
        match = re.search(r"^> Content version \*\*([0-9]+\.[0-9]+\.[0-9]+)\*\*", text[end + 4 :], re.MULTILINE)
        if not match:
            return None, f"{skill_md}: no ordinary-Markdown Content version marker"
    else:
        match = re.search(r"^version:\s*([0-9]+\.[0-9]+\.[0-9]+)\s*$", text[3:end], re.MULTILINE)
        if not match:
            return None, f"{skill_md}: no content version in frontmatter"
    return match.group(1), None


def identity_from_entries(entries):
    entries.sort(key=lambda item: item[0].encode("utf-8"))
    inventory = b"".join(
        hashlib.sha256(content).hexdigest().encode("ascii")
        + b"\t"
        + str(len(content)).encode("ascii")
        + b"\t"
        + relative.encode("utf-8")
        + b"\n"
        for relative, content in entries
    )
    return {
        "payload_sha256": hashlib.sha256(inventory).hexdigest(),
        "file_count": len(entries),
        "total_bytes": sum(len(content) for _, content in entries),
    }


def payload_identity(plugin_dir):
    """Return the exact sha256-size-path-v1 aggregate for a plugin tree."""
    entries = []
    for base, dirnames, filenames in os.walk(plugin_dir, followlinks=False):
        for name in sorted(dirnames + filenames):
            path = os.path.join(base, name)
            relative = os.path.relpath(path, plugin_dir).replace(os.sep, "/")
            if os.path.islink(path):
                raise ValueError(f"plugin payload contains symlink: {relative}")
        for name in filenames:
            path = os.path.join(base, name)
            relative = os.path.relpath(path, plugin_dir).replace(os.sep, "/")
            if "\t" in relative or "\n" in relative or "\r" in relative:
                raise ValueError(f"plugin payload has unsafe path: {relative!r}")
            if not os.path.isfile(path):
                raise ValueError(f"plugin payload member is not regular: {relative}")
            with open(path, "rb") as handle:
                content = handle.read()
            entries.append((relative, content))
    return identity_from_entries(entries)


def git_bytes(*arguments):
    return subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    ).stdout


def payload_identity_at_commit(plugin_name, commit):
    """Rebuild a plugin payload identity from immutable Git blob bytes."""
    prefix = f"plugins/{plugin_name}/"
    tree = git_bytes("ls-tree", "-r", "-z", commit, "--", prefix)
    entries = []
    for raw_record in tree.rstrip(b"\0").split(b"\0") if tree else []:
        metadata, raw_path = raw_record.split(b"\t", 1)
        mode, object_type, _object_id = metadata.decode("ascii").split()
        if object_type != "blob" or mode not in {"100644", "100755"}:
            raise ValueError(f"published plugin payload member is not regular: {raw_path!r}")
        path = raw_path.decode("utf-8")
        if not path.startswith(prefix):
            raise ValueError(f"published plugin path escapes prefix: {path}")
        relative = path[len(prefix) :]
        if any(character in relative for character in ("\t", "\n", "\r")):
            raise ValueError(f"published plugin payload has unsafe path: {relative!r}")
        entries.append((relative, git_bytes("show", f"{commit}:{path}")))
    if not entries:
        raise ValueError(f"published plugin payload is empty: {plugin_name}")
    return identity_from_entries(entries)


def release_identity_at_commit(plugin_name, commit):
    """Reproduce one complete plugin release identity from immutable Git bytes."""
    manifest = json.loads(
        git_bytes(
            "show", f"{commit}:plugins/{plugin_name}/.claude-plugin/plugin.json"
        ).decode("utf-8")
    )
    skill_text = git_bytes(
        "show", f"{commit}:plugins/{plugin_name}/SKILL.md"
    ).decode("utf-8")
    frontmatter_end = skill_text.find("\n---", 3)
    frontmatter_match = (
        re.search(
            r"^version:\s*([0-9]+\.[0-9]+\.[0-9]+)\s*$",
            skill_text[3:frontmatter_end],
            re.MULTILINE,
        )
        if frontmatter_end != -1
        else None
    )
    markdown_match = re.search(
        r"^> Content version \*\*([0-9]+\.[0-9]+\.[0-9]+)\*\*",
        skill_text[frontmatter_end + 4 :] if frontmatter_end != -1 else "",
        re.MULTILINE,
    )
    version_match = frontmatter_match or markdown_match
    if version_match is None:
        raise ValueError(f"committed SKILL has no content version: {plugin_name}@{commit}")
    return {
        "version": manifest.get("version"),
        "content_version": version_match.group(1),
        **payload_identity_at_commit(plugin_name, commit),
    }


def immutable_release_errors(plugin_name, release, *, is_current):
    found = []
    if not isinstance(release, dict) or set(release) != {
        "version",
        "content_version",
        "source_commit",
        "payload_sha256",
        "file_count",
        "total_bytes",
    }:
        return ["release row has missing or undeclared fields"]
    commit = release.get("source_commit")
    if commit == "working-tree-release":
        if not is_current:
            found.append("only the current release may use working-tree-release")
        return found
    if not isinstance(commit, str) or not GIT_COMMIT.fullmatch(commit):
        return ["release source_commit must be an immutable full Git SHA"]
    try:
        reproduced = release_identity_at_commit(plugin_name, commit)
    except (
        OSError,
        ValueError,
        UnicodeError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as exc:
        return [f"cannot reproduce immutable release {commit}: {exc}"]
    for field in (
        "version",
        "content_version",
        "payload_sha256",
        "file_count",
        "total_bytes",
    ):
        if release.get(field) != reproduced.get(field):
            found.append(f"immutable release {commit} {field} differs from Git bytes")
    return found


def base_release_errors(base, version, skill_version, payload):
    found = []
    if base.get("source_commit") != PUBLISHED_BASE:
        found.append("base release is not tied to the fixed published commit")
    if base.get("version") != version:
        found.append("base plugin version differs from the published manifest")
    if base.get("content_version") != skill_version:
        found.append("base content version differs from the published SKILL")
    for key in ("payload_sha256", "file_count", "total_bytes"):
        if base.get(key) != payload.get(key):
            found.append(f"base plugin payload {key} differs from published Git bytes")
    return found


def release_identity_errors(
    plugin_ledger, actual_version, actual_content_version, actual_payload
):
    found = []
    releases = plugin_ledger.get("releases") if isinstance(plugin_ledger, dict) else None
    if not isinstance(releases, list) or len(releases) < 2:
        return ["release ledger needs at least a fixed base and current release"]
    versions = [release.get("version") for release in releases if isinstance(release, dict)]
    if len(versions) != len(releases) or len(set(versions)) != len(versions):
        found.append("release ledger versions must be present and unique")
        return found
    current = releases[-1]
    prior = releases[-2]
    if current.get("version") != actual_version:
        found.append("current plugin version is not the release ledger's latest version")
    if current.get("content_version") != actual_content_version:
        found.append("current SKILL content version is not bound to the latest release")
    for key in ("payload_sha256", "file_count", "total_bytes"):
        if current.get(key) != actual_payload.get(key):
            found.append(f"current plugin payload {key} differs from release ledger")
    if prior.get("version") == current.get("version"):
        found.append("functional payload release did not change plugin version")
    if prior.get("content_version") == current.get("content_version"):
        found.append("functional payload release did not change SKILL content version")
    if prior.get("payload_sha256") == current.get("payload_sha256"):
        found.append("release ledger does not record a functional payload change")
    return found


def validate_release_ledger(run_self_test=False):
    ledger = load_json(RELEASE_LEDGER)
    if ledger is None:
        return
    if ledger.get("schema_version") != 1:
        err("release ledger must declare schema_version 1")
    if ledger.get("payload_format") != PAYLOAD_FORMAT:
        err(f"release ledger payload_format must be {PAYLOAD_FORMAT}")
    plugins = ledger.get("plugins")
    if not isinstance(plugins, dict) or set(plugins) != ROUTED_PLUGINS:
        err("release ledger must contain every routed plugin exactly once")
        return
    expected_required_paths = {
        INSIGHT_PLUGIN: ["assets/tiebreaker_prompt_template.md"],
        "overnight-multi-issue-implementation": [
            "references/large-live-queue-orchestration.md"
        ],
        "overnight-review-client-delivery": [
            "scripts/action_authority.py",
            "scripts/final_byte_review.py",
        ],
        "schedule-poll-orchestrator-pattern": ["scripts/poll_orchestrator.py"],
    }
    for plugin_name in sorted(ROUTED_PLUGINS):
        plugin_ledger = plugins[plugin_name]
        required_paths = (
            plugin_ledger.get("required_current_paths")
            if isinstance(plugin_ledger, dict)
            else None
        )
        if required_paths != expected_required_paths.get(plugin_name, []):
            err(f"{plugin_name}: release ledger has wrong required payload paths")
            continue
        releases = plugin_ledger.get("releases")
        if not isinstance(releases, list) or len(releases) < 2:
            err(f"{plugin_name}: release ledger needs a published base and current release")
            continue
        base = releases[0]
        plugin_dir = os.path.join(ROOT, "plugins", plugin_name)
        for relative in required_paths:
            required_path = os.path.join(plugin_dir, *relative.split("/"))
            if os.path.islink(required_path) or not os.path.isfile(required_path):
                err(f"{plugin_name}: required release payload is missing or unsafe: {relative}")
        manifest = load_json(os.path.join(plugin_dir, ".claude-plugin", "plugin.json"))
        if manifest is None:
            continue
        current_content_version, content_error = content_version(
            os.path.join(plugin_dir, "SKILL.md"),
            require_codex_frontmatter=plugin_name == INSIGHT_PLUGIN,
        )
        if content_error:
            err(content_error)
            continue
        try:
            payload = payload_identity(plugin_dir)
            base_identity = release_identity_at_commit(plugin_name, PUBLISHED_BASE)
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
            err(f"{plugin_name}: cannot reproduce published base identity: {exc}")
            continue

        base_errors = base_release_errors(
            base,
            base_identity["version"],
            base_identity["content_version"],
            base_identity,
        )
        baseline_errors = release_identity_errors(
            plugin_ledger, manifest.get("version"), current_content_version, payload
        )
        for message in base_errors + baseline_errors:
            err(f"{plugin_name}: {message}")
        immutable_errors = []
        for index, release in enumerate(releases):
            immutable_errors.extend(
                immutable_release_errors(
                    plugin_name, release, is_current=index == len(releases) - 1
                )
            )
        for message in immutable_errors:
            err(f"{plugin_name}: {message}")

        if run_self_test and not (base_errors or baseline_errors or immutable_errors):
            drifted = dict(payload)
            digest = payload["payload_sha256"]
            drifted["payload_sha256"] = (
                ("0" if digest[0] != "0" else "1") + digest[1:]
            )
            if not release_identity_errors(
                plugin_ledger,
                manifest.get("version"),
                current_content_version,
                drifted,
            ):
                err(f"{plugin_name}: one-byte identity-drift control did not fail")
            if not release_identity_errors(
                plugin_ledger,
                base.get("version"),
                current_content_version,
                payload,
            ):
                err(f"{plugin_name}: unchanged-version control did not fail")
            drifted_base = dict(base)
            base_digest = base["payload_sha256"]
            drifted_base["payload_sha256"] = (
                ("0" if base_digest[0] != "0" else "1") + base_digest[1:]
            )
            if not base_release_errors(
                drifted_base,
                base_identity["version"],
                base_identity["content_version"],
                base_identity,
            ):
                err(f"{plugin_name}: published-base drift control did not fail")
            immutable_rows = [
                release
                for release in releases
                if release.get("source_commit") != "working-tree-release"
            ]
            if immutable_rows:
                drifted_middle = dict(immutable_rows[-1])
                middle_digest = drifted_middle["payload_sha256"]
                drifted_middle["payload_sha256"] = (
                    ("0" if middle_digest[0] != "0" else "1") + middle_digest[1:]
                )
                if not immutable_release_errors(
                    plugin_name,
                    drifted_middle,
                    is_current=drifted_middle is releases[-1],
                ):
                    err(f"{plugin_name}: immutable middle-release drift control did not fail")
            if len(releases) > 1:
                invalid_sentinel = dict(releases[0])
                invalid_sentinel["source_commit"] = "working-tree-release"
                if not immutable_release_errors(
                    plugin_name, invalid_sentinel, is_current=False
                ):
                    err(f"{plugin_name}: historical working-tree sentinel control did not fail")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run release-identity negative controls",
    )
    args = parser.parse_args()
    mkt_path = os.path.join(ROOT, ".claude-plugin", "marketplace.json")
    if not os.path.isfile(mkt_path):
        err(".claude-plugin/marketplace.json not found")
        return finish()
    mkt = load_json(mkt_path)
    if mkt is None:
        return finish()
    for key in ("name", "owner", "plugins"):
        if key not in mkt:
            err(f"marketplace.json missing top-level `{key}`")
    plugins = mkt.get("plugins", [])
    registered = {}
    marketplace_entries = {}
    for p in plugins:
        name, source = p.get("name"), p.get("source", "")
        if not name or not source:
            err(f"marketplace plugin entry missing name/source: {p}")
            continue
        if name in registered:
            err(f"plugin `{name}` registered more than once in marketplace.json")
        registered[name] = source.lstrip("./")
        marketplace_entries[name] = p

    plugins_dir = os.path.join(ROOT, "plugins")
    on_disk = set()
    if os.path.isdir(plugins_dir):
        on_disk = {d for d in os.listdir(plugins_dir)
                   if os.path.isdir(os.path.join(plugins_dir, d))}

    # orphan dirs not in marketplace
    for d in sorted(on_disk - {os.path.basename(s) for s in registered.values()}):
        err(f"plugins/{d} exists on disk but is not registered in marketplace.json")

    for name, rel in registered.items():
        pdir = os.path.join(ROOT, rel)
        if not os.path.isdir(pdir):
            err(f"marketplace source `{rel}` (plugin {name}) does not exist")
            continue
        pj = os.path.join(pdir, ".claude-plugin", "plugin.json")
        if not os.path.isfile(pj):
            err(f"{rel}: missing .claude-plugin/plugin.json")
        else:
            pjd = load_json(pj)
            if pjd is not None:
                if pjd.get("name") != os.path.basename(rel):
                    err(f"{pj}: name `{pjd.get('name')}` != dir `{os.path.basename(rel)}`")
                if pjd.get("name") != name:
                    err(f"{pj}: name `{pjd.get('name')}` != marketplace entry `{name}`")
                if not pjd.get("version"):
                    warn(f"{pj}: no version field")
                elif pjd.get("version") != marketplace_entries[name].get("version"):
                    err(
                        f"{pj}: version `{pjd.get('version')}` != marketplace "
                        f"version `{marketplace_entries[name].get('version')}`"
                    )
        # skill presence: flat SKILL.md or nested skills/*/SKILL.md
        flat = os.path.join(pdir, "SKILL.md")
        skills_dir = os.path.join(pdir, "skills")
        if os.path.isfile(flat):
            check_skill(flat)
        elif os.path.isdir(skills_dir):
            subs = [d for d in os.listdir(skills_dir)
                    if os.path.isdir(os.path.join(skills_dir, d))]
            if not subs:
                err(f"{rel}/skills/ has no skill subdirectories")
            for d in subs:
                sm = os.path.join(skills_dir, d, "SKILL.md")
                if not os.path.isfile(sm):
                    err(f"{rel}/skills/{d}/ missing SKILL.md")
                else:
                    check_skill(sm)
        else:
            err(f"{rel}: no SKILL.md and no skills/ directory")

    validate_release_ledger(args.self_test)

    # VERSION drift guard
    vpath = os.path.join(ROOT, "VERSION")
    if os.path.isfile(vpath):
        with open(vpath, encoding="utf-8") as f:
            version = f.read().strip()
        if not version:
            err("VERSION file is empty")
        elif len(registered) == 1:
            only = next(iter(registered.values()))
            pjd = load_json(os.path.join(ROOT, only, ".claude-plugin", "plugin.json"))
            if pjd and pjd.get("version") and pjd["version"] != version:
                err(f"VERSION ({version}) != single plugin version ({pjd['version']})")
    return finish()


def check_skill(skill_md):
    n = frontmatter_name(skill_md)
    if n is not None:
        dirname = os.path.basename(os.path.dirname(skill_md))
        if n != dirname:
            err(f"{skill_md}: frontmatter name `{n}` != dir `{dirname}`")


def finish():
    for w in warnings:
        print(f"::warning::{w}")
    if errors:
        for e in errors:
            print(f"::error::{e}")
        print(f"\nFAILED: {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    print(f"OK: marketplace + plugins valid ({len(warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
