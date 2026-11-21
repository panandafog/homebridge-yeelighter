"""Lenovo controller; internal commands run in a disposable, offline container."""
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import uuid

PLUGIN = "homebridge-yeelighter"
CONTAINER = "homebridge_homebridge_1"
ROOT = Path(__file__).resolve().parent


def run(args, **kwargs):
    try:
        return subprocess.run([str(a) for a in args], check=True, **kwargs)
    except subprocess.CalledProcessError as exc:
        if kwargs.get("capture_output"):
            if exc.stdout:
                print(exc.stdout, file=sys.stderr, end="")
            if exc.stderr:
                print(exc.stderr, file=sys.stderr, end="")
        raise


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n")
    tmp.replace(path)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hashes(path):
    return {str(p.relative_to(path)): sha(p) for p in sorted(path.rglob("*")) if p.is_file()}


def identity(data):
    info, identifiers, accessories, hosts = {}, {}, {}, set()
    for p in (data / "persist").glob("AccessoryInfo.*.json"):
        obj = read(p)
        stable = {k: obj.get(k) for k in ["pincode", "signSk", "signPk", "pairedClients", "pairedClientsPermission", "setupID"]}
        info[p.name] = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
    for p in (data / "persist").glob("IdentifierCache.*.json"):
        identifiers[p.name] = {k: v for k, v in read(p)["cache"].items() if not k.endswith("|nextIID")}
    for p in (data / "accessories").glob("cachedAccessories*"):
        arr = read(p)
        accessories[p.name] = sorted([a["UUID"], a.get("plugin"), a.get("platform")] for a in arr)
        for a in arr:
            if a.get("plugin") == PLUGIN:
                host = a.get("context", {}).get("device", {}).get("host")
                if host:
                    hosts.add(host)
    return {"configSha256": sha(data / "config.json"), "pairing": info, "identifiers": identifiers, "accessories": accessories, "hosts": sorted(hosts)}


def proof(before, after):
    return {
        "config_unchanged": before["configSha256"] == after["configSha256"],
        "pairing_keys_unchanged": before["pairing"] == after["pairing"],
        "existing_homekit_ids_unchanged": all(after["identifiers"].get(f, {}).get(k) == v for f, entries in before["identifiers"].items() for k, v in entries.items()),
        "accessory_uuids_unchanged": before["accessories"] == after["accessories"],
    }


def extract(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        for item in tar.getmembers():
            p = Path(item.name)
            assert not p.is_absolute() and ".." not in p.parts, "Unsafe archive path"
            assert item.isfile() or item.isdir(), "Only regular files/directories are supported"
        tar.extractall(destination)


def verify_backup(backup):
    meta = read(backup / "manifest.json")
    assert all(sha(backup / p) == h for p, h in meta["sha256"].items()), "Backup checksum mismatch"
    return meta


def snapshot(data, backup):
    backup.mkdir(parents=True)
    excluded = {"node_modules", "backups", "homebridge.log", ".homelab-releases", ".homelab-transactions"}
    paths = sorted(p.name for p in data.iterdir() if p.name not in excluded)
    with tarfile.open(backup / "state.tar.gz", "w:gz") as tar:
        for name in paths:
            tar.add(data / name, arcname=name)
    with tarfile.open(backup / "plugin.tar.gz", "w:gz") as tar:
        tar.add(data / "node_modules" / PLUGIN, arcname=PLUGIN)
    write(backup / "identity.json", identity(data))
    meta = {"id": backup.name, "version": read(data / "node_modules" / PLUGIN / "package.json")["version"], "state_paths": paths, "plugin_hashes": hashes(data / "node_modules" / PLUGIN)}
    meta["sha256"] = hashes(backup)
    write(backup / "manifest.json", meta)
    return {"id": backup.name, "version": meta["version"], "backup_verified": bool(verify_backup(backup))}


def activate(data, release, backup, work):
    meta = verify_backup(backup)
    assert hashes(data / "node_modules" / PLUGIN) == meta["plugin_hashes"], "Installed plugin differs from backup"
    assert all(proof(read(backup / "identity.json"), identity(data)).values()), "HomeKit/configuration changed since backup"
    preflight = read(release / "preflight.json")
    assert preflight["passed"]
    candidate = work / preflight["stage"] / "node_modules" / PLUGIN
    assert hashes(candidate) == preflight["plugin_hashes"], "Staged package changed"
    transaction = data / ".homelab-transactions" / uuid.uuid4().hex
    transaction.mkdir(parents=True)
    shutil.copytree(candidate, transaction / "new")
    (data / "node_modules" / PLUGIN).rename(transaction / "old")
    try:
        (transaction / "new").rename(data / "node_modules" / PLUGIN)
        package = read(data / "package.json")
        archive = read(release / "release.json")["archive"]
        destination = data / ".homelab-releases" / (release.name + ".tgz")
        destination.parent.mkdir(exist_ok=True)
        shutil.copy2(release / archive, destination)
        package["dependencies"][PLUGIN] = "file:.homelab-releases/" + destination.name
        write(data / "package.json", package)
    except BaseException:
        if (data / "node_modules" / PLUGIN).exists():
            shutil.rmtree(data / "node_modules" / PLUGIN)
        (transaction / "old").rename(data / "node_modules" / PLUGIN)
        raise
    return {"installed_version": read(data / "node_modules" / PLUGIN / "package.json")["version"], "identity": proof(read(backup / "identity.json"), identity(data))}


def restore(data, backup):
    meta = verify_backup(backup)
    expected = read(backup / "identity.json")
    assert sha(data / "config.json") == expected["configSha256"], "Configuration was edited after backup; refusing to overwrite it"
    transaction = data / ".homelab-transactions" / uuid.uuid4().hex
    extract(backup / "plugin.tar.gz", transaction / "plugin")
    extract(backup / "state.tar.gz", transaction / "state")
    assert hashes(transaction / "plugin" / PLUGIN) == meta["plugin_hashes"]
    current = data / "node_modules" / PLUGIN
    changed = []
    current.rename(transaction / "superseded-plugin")
    try:
        (transaction / "plugin" / PLUGIN).rename(current)
        for name in meta["state_paths"]:
            dest = data / name
            existed = dest.exists()
            if existed:
                dest.rename(transaction / ("superseded-" + name))
            changed.append((name, existed))
            (transaction / "state" / name).rename(dest)
        result = proof(expected, identity(data))
        assert all(result.values()), "Restored identity differs from backup"
        assert hashes(current) == meta["plugin_hashes"]
    except BaseException:
        for name, existed in reversed(changed):
            dest = data / name
            if dest.is_dir():
                shutil.rmtree(dest)
            elif dest.exists():
                dest.unlink()
            if existed:
                (transaction / ("superseded-" + name)).rename(dest)
        if current.exists():
            shutil.rmtree(current)
        (transaction / "superseded-plugin").rename(current)
        raise
    return {"restored_version": meta["version"], "identity": result, "plugin_hashes_match": True}


def internal(mode, args):
    data, work = Path("/data"), Path("/work")
    if mode == "snapshot":
        return snapshot(data, work / "backups" / args[0])
    if mode == "activate":
        return activate(data, work / "releases" / args[0], work / "backups" / args[1], work)
    if mode == "restore":
        return restore(data, work / "backups" / args[0])
    if mode == "proof":
        backup = work / "backups" / args[0]
        return {"version": read(data / "node_modules" / PLUGIN / "package.json")["version"], "identity": proof(read(backup / "identity.json"), identity(data))}
    if mode == "rehearse":
        release, backup = work / "releases" / args[0], work / "backups" / args[1]
        clone = Path(tempfile.mkdtemp(prefix="rollback-", dir=work / "rehearsals"))
        extract(backup / "state.tar.gz", clone)
        extract(backup / "plugin.tar.gz", clone / "node_modules")
        activate(clone, release, backup, work)
        # Simulate state written by a test version, never published to HomeKit.
        (clone / "persist" / "test-only-marker").write_text("test")
        restored = restore(clone, backup)
        assert not (clone / "persist" / "test-only-marker").exists()
        shutil.rmtree(clone)
        return {"rollback_rehearsal_passed": True, "restored": restored, "production_modified": False}
    if mode == "preflight":
        release = work / "releases" / args[0]
        meta = read(release / "release.json")
        assert all(sha(release / p) == h for p, h in meta["sha256"].items())
        stage = Path(tempfile.mkdtemp(prefix=release.name + "-", dir=work / "staging"))
        log = release / "preflight.log"
        env = {**os.environ, "NODE_PATH": "/baseline_modules", "YEELIGHTER_TEST_DIST": str(stage / "node_modules" / PLUGIN / "dist")}
        with log.open("w") as output:
            run(["npm", "install", "--prefix", stage, "--offline", "--omit=dev", "--ignore-scripts", "--package-lock=false", "--no-audit", "--no-fund", release / meta["archive"]], stdout=output, stderr=subprocess.STDOUT)
            run(["node", release / "test/lightservice.cjs"], env=env, stdout=output, stderr=subprocess.STDOUT)
            run(["node", "--test", release / "test/reconnect.cjs", release / "test/discovery.cjs"], env=env, stdout=output, stderr=subprocess.STDOUT)
        package = stage / "node_modules" / PLUGIN
        assert read(package / "package.json")["version"] == meta["version"]
        result = {"passed": True, "version": meta["version"], "stage": str(stage.relative_to(work)), "plugin_hashes": hashes(package)}
        write(release / "preflight.json", result)
        return {"preflight_passed": True, "version": meta["version"], "network": "none"}
    raise ValueError(mode)


def context():
    obj = json.loads(run(["docker", "inspect", CONTAINER], capture_output=True, text=True).stdout)[0]
    volume = next(m["Source"] for m in obj["Mounts"] if m["Destination"] == "/homebridge")
    assert obj["State"]["Running"], "Homebridge must be running before this command"
    return obj["Image"], volume


def helper(image, volume, mode, args, writable=False):
    command = ["docker", "run", "--rm", "--pull=never", "--network=none", "--entrypoint", "python3", "--mount", "type=bind,src=" + str(ROOT) + ",dst=/work", "--mount", "type=bind,src=" + str(volume) + ",dst=/data" + ("" if writable else ",readonly")]
    command += ["--mount", "type=bind,src=" + str(Path(volume) / "node_modules") + ",dst=/baseline_modules,readonly", image, "/work/homelab_remote.py", "internal", mode, *args]
    result = run(command, capture_output=True, text=True)
    return json.loads(result.stdout.strip().splitlines()[-1])


def health(image, volume, backup_id, version, timeout=90):
    deadline = time.monotonic() + timeout
    expected = read(ROOT / "backups" / backup_id / "identity.json")["hosts"]
    error = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8581/", timeout=2) as response:
                assert response.status == 200
            sockets = run(["ss", "-tn", "state", "established", "( dport = :55443 )"], capture_output=True, text=True).stdout
            assert all(host + ":55443" in sockets for host in expected), "Waiting for lamp connections"
            result = helper(image, volume, "proof", [backup_id])
            assert result["version"] == version
            assert all(result["identity"].values()), "HomeKit identity changed"
            return {**result, "ui_http": 200, "connected_lamps": expected}
        except Exception as exc:
            error = str(exc)
            time.sleep(3)
    raise RuntimeError("Health check failed: " + str(error))


def switch(image, volume, mode, args):
    run(["docker", "stop", "-t", "15", CONTAINER], capture_output=True)
    try:
        return helper(image, volume, mode, args, writable=True)
    finally:
        run(["docker", "start", CONTAINER], capture_output=True)


def main():
    os.umask(0o077)
    if sys.argv[1] == "internal":
        try:
            result = internal(sys.argv[2], sys.argv[3:])
        finally:
            # Let the SSH user copy/verify private backups made by container root.
            work = Path("/work")
            owner = work.stat()
            for p in work.rglob("*"):
                if not p.is_symlink():
                    os.chown(p, owner.st_uid, owner.st_gid)
        print(json.dumps(result))
        return
    for name in ["backups", "releases", "staging", "rehearsals"]:
        (ROOT / name).mkdir(exist_ok=True)
    with (ROOT / ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        image, volume = context()
        mode, args = sys.argv[1], sys.argv[2:]
        if mode == "preflight":
            result = helper(image, volume, mode, args)
        elif mode == "backup":
            backup_id = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + "-" + uuid.uuid4().hex[:8]
            run(["docker", "pause", CONTAINER], capture_output=True)
            try:
                result = helper(image, volume, "snapshot", [backup_id])
            finally:
                run(["docker", "unpause", CONTAINER], capture_output=True)
            write(ROOT / "backups" / backup_id / "environment.json", {"image": image, "volume": volume, "container": CONTAINER})
        elif mode == "rehearse":
            result = helper(image, volume, mode, args)
        elif mode == "activate":
            release_id, backup_id = args
            try:
                switch(image, volume, "activate", args)
                result = health(image, volume, backup_id, read(ROOT / "releases" / release_id / "release.json")["version"])
                write(ROOT / "active.json", {"release": release_id, "backup": backup_id})
            except BaseException:
                switch(image, volume, "restore", [backup_id])
                result = health(image, volume, backup_id, read(ROOT / "backups" / backup_id / "manifest.json")["version"])
                write(ROOT / "failed-deployment.json", {"rolled_back": True, "backup": backup_id, "health": result})
                write(ROOT / "active.json", {"backup": backup_id, "rolled_back": True, "failed_release": release_id})
                print("Automatic rollback completed; previous version and HomeKit identity verified", file=sys.stderr)
                raise
        elif mode == "rollback":
            backup_id = args[0] if args else read(ROOT / "active.json")["backup"]
            switch(image, volume, "restore", [backup_id])
            result = health(image, volume, backup_id, read(ROOT / "backups" / backup_id / "manifest.json")["version"])
            write(ROOT / "active.json", {"backup": backup_id, "rolled_back": True})
        elif mode == "status":
            active = read(ROOT / "active.json") if (ROOT / "active.json").exists() else {}
            result = {"active": active, "image": image}
            if active:
                result.update(helper(image, volume, "proof", [active["backup"]]))
        else:
            raise ValueError(mode)
        print(json.dumps(result))


if __name__ == "__main__":
    main()
