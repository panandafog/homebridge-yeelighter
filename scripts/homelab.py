"""Build, deliver and roll back this fork. Requires Python 3, pnpm and SSH."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build():
    assert not run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout, "Commit changes first"
    run([sys.executable, "-m", "unittest", "discover", "-s", "test", "-p", "*.py"], cwd=ROOT)
    run(["pnpm", "test"], cwd=ROOT)
    sha = run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    package = json.loads((ROOT / "package.json").read_text())
    release = ROOT / ".homelab/releases" / (package["version"] + "-" + sha[:12])
    assert not release.exists(), "Release already exists; use it or bump the version"
    release.mkdir(parents=True)
    stage = release / "package"
    stage.mkdir()
    for name in ["dist", "config.schema.json", "README.md", "LICENSE"]:
        src = ROOT / name
        if src.is_dir():
            shutil.copytree(src, stage / name)
        else:
            shutil.copy2(src, stage / name)
    # These two runtime dependencies are pure JavaScript. Bundle regular files,
    # never pnpm symlinks or Mac native/development dependencies.
    resolve = "const{createRequire}=require('node:module');const h=require.resolve('http-headers/package.json');console.log(JSON.stringify([h,createRequire(h).resolve('next-line/package.json')]))"
    paths = json.loads(run(["node", "-e", resolve], cwd=ROOT, capture_output=True, text=True).stdout)
    header = stage / "node_modules/http-headers"
    shutil.copytree(Path(paths[0]).parent, header, ignore=shutil.ignore_patterns("node_modules"))
    shutil.copytree(Path(paths[1]).parent, header / "node_modules/next-line", ignore=shutil.ignore_patterns("node_modules"))
    package["dependencies"]["http-headers"] = json.loads(Path(paths[0]).read_text())["version"]
    package["bundleDependencies"] = ["http-headers"]
    package["homelabBuild"] = {"gitSha": sha}
    package.pop("scripts", None)
    package.pop("devDependencies", None)
    (stage / "package.json").write_text(json.dumps(package, indent=2) + "\n")
    run(["pnpm", "--config.node-linker=hoisted", "pack", "--pack-destination", release], cwd=stage)
    archive = next(release.glob("*.tgz"))
    with tarfile.open(archive) as tar:
        assert tar.getmember("package/dist/index.js")
        assert tar.getmember("package/node_modules/http-headers/node_modules/next-line/package.json")
    shutil.rmtree(stage)
    shutil.copytree(ROOT / "test", release / "test")
    shutil.copy2(ROOT / "scripts/homelab_remote.py", release / "homelab_remote.py")
    manifest = {"id": release.name, "version": package["version"], "gitSha": sha, "archive": archive.name}
    manifest["sha256"] = {str(p.relative_to(release)): digest(p) for p in release.rglob("*") if p.is_file()}
    (release / "release.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(release)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["build", "preflight", "deploy", "rollback", "status"])
    parser.add_argument("release_or_backup", nargs="?")
    parser.add_argument("--target", default="homelab-lenovo")
    parser.add_argument("--remote-root", default="/home/panandafog/Software/homebridge-homelab")
    parser.add_argument("--backup-root", type=Path, default=Path.home() / "Software/homelab/backups/homebridge")
    args = parser.parse_args()
    os.umask(0o077)
    if args.action == "build":
        build()
        return
    remote = args.remote_root

    def ssh(command):
        result = run(["ssh", args.target, command], capture_output=True, text=True)
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        lines = result.stdout.strip().splitlines()
        return json.loads(lines[-1])

    controller = remote + "/homelab_remote.py"
    if args.action in ["preflight", "deploy"]:
        release = Path(args.release_or_backup).resolve()
        meta = json.loads((release / "release.json").read_text())
        assert all(digest(release / p) == h for p, h in meta["sha256"].items()), "Release checksum mismatch"
        destination = remote + "/releases/" + meta["id"]
        run(["ssh", args.target, shlex.join(["mkdir", "-p", destination])])
        run(["scp", "-r", *sorted(release.iterdir()), args.target + ":" + destination + "/"])
        run(["scp", ROOT / "scripts/homelab_remote.py", args.target + ":" + controller])
        preflight = ssh(shlex.join(["python3", controller, "preflight", meta["id"]]))
        print(json.dumps(preflight))
        if args.action == "preflight":
            return
        backup = ssh(shlex.join(["python3", controller, "backup"]))
        local = args.backup_root / ("before-" + backup["id"])
        local.mkdir(parents=True)
        backup_files = ["manifest.json", "identity.json", "plugin.tar.gz", "state.tar.gz", "environment.json"]
        run(["scp", *[args.target + ":" + remote + "/backups/" + backup["id"] + "/" + name for name in backup_files], str(local) + "/"])
        hashes = json.loads((local / "manifest.json").read_text())["sha256"]
        assert all(digest(local / p) == h for p, h in hashes.items()), "Backup checksum mismatch; deployment cancelled"
        shutil.copy2(ROOT / "scripts/homelab_remote.py", local / "homelab_remote.py")
        rollback = "ssh " + shlex.quote(args.target) + " " + shlex.quote(shlex.join(["python3", controller, "rollback", backup["id"]]))
        (local / "ROLLBACK.txt").write_text("Restore the exact working plugin, configuration, pairing and accessory state:\n" + rollback + "\n\nDo not remove/re-add Homebridge or lights in Apple Home.\n")
        for p in local.rglob("*"):
            p.chmod(0o700 if p.is_dir() else 0o600)
        print("Backup verified on Mac: " + str(local), flush=True)
        rehearsal = ssh(shlex.join(["python3", controller, "rehearse", meta["id"], backup["id"]]))
        (local / "rollback-rehearsal.json").write_text(json.dumps(rehearsal, indent=2) + "\n")
        assert rehearsal["rollback_rehearsal_passed"], "Rollback rehearsal failed; deployment cancelled"
        result = ssh(shlex.join(["python3", controller, "activate", meta["id"], backup["id"]]))
        (local / "deployment-result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
    else:
        command = ["python3", controller, args.action]
        if args.release_or_backup:
            command.append(args.release_or_backup)
        print(json.dumps(ssh(shlex.join(command)), indent=2))


if __name__ == "__main__":
    main()
