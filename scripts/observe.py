"""Passive Homebridge observation on Lenovo; no direct lamp connections."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
CONTAINER = "homebridge_homebridge_1"
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def save(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n")
    temp.replace(path)


def events(lines, state, seen):
    for raw in lines:
        line = ANSI.sub("", raw).strip()
        key = hashlib.sha256(line.encode()).hexdigest()
        if key in seen or not line:
            continue
        seen.add(key)
        try:
            # Docker UTC timestamps include nanoseconds; datetime needs only micros.
            stamp = line.split(" ", 1)[0]
            stamp = re.sub(r"(\.\d{6})\d+Z$", r"\1Z", stamp)
            when = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
        except (ValueError, IndexError):
            continue
        device = re.search(r"\[(0x[0-9a-f]+)\]", line)
        if device and re.search(r"\bDisconnected\b", line):
            name = device.group(1)
            if name not in state["disconnected_since"]:
                state["disconnected_since"][name] = when
                state["disconnect_count"][name] = state["disconnect_count"].get(name, 0) + 1
        elif device and re.search(r"\bConnected\b", line):
            name = device.group(1)
            start = state["disconnected_since"].pop(name, None)
            if start is not None:
                state["recoveries"].append({"device": name, "seconds": round(when - start, 3), "at_utc": stamp})
        if re.search(r"Disconnected|\bConnected\b|Socket closed|No update received|heartbeat timed out|failed to set|quota exceeded|EHOSTUNREACH|ECONNRESET|Unhandled|TypeError|SyntaxError", line):
            state["recent_events"].append(line)
            state["recent_events"] = state["recent_events"][-30:]


def initial():
    return {"disconnect_count": {}, "disconnected_since": {}, "recoveries": [], "recent_events": []}


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=8)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return result.stdout


def observe(out, seconds):
    import urllib.request
    meta = json.loads((out / "run.json").read_text())
    state = {**initial(), "start": meta["start"], "duration_seconds": seconds, "samples": 0, "ui_failures": 0, "socket_absent_samples": {}, "last_sample": None, "complete": False}
    seen = set()
    stopped = False
    def stop(*_):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log = (out / "homebridge.log").open("a")
    reader = (out / "homebridge.log").open()
    samples = (out / "samples.jsonl").open("a")
    follower = None
    begin = time.monotonic()
    next_version = begin
    try:
        while not stopped and time.monotonic() - begin < seconds:
            cycle = time.monotonic()
            if follower is None or follower.poll() is not None:
                # Re-read from the beginning timestamp after a container restart.
                # Event de-duplication prevents counting replayed log lines twice.
                follower = subprocess.Popen(["docker", "logs", "--timestamps", "--since", meta["start"], "--follow", CONTAINER], stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            sample = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
            try:
                sample["sockets"] = command(["ss", "-tin", "state", "established", "( dport = :55443 )"])
                for host in meta["hosts"]:
                    if host + ":55443" not in sample["sockets"]:
                        counts = state["socket_absent_samples"]
                        counts[host] = counts.get(host, 0) + 1
                with urllib.request.urlopen("http://127.0.0.1:8581/", timeout=3) as response:
                    sample["ui_http"] = response.status
            except Exception as exc:
                sample["error"] = str(exc)
                state["ui_failures"] += 1
            if cycle >= next_version:
                try:
                    sample["container"] = command(["docker", "inspect", "--format", "{{.State.Status}} {{.State.StartedAt}}", CONTAINER]).strip()
                    sample["version"] = command(["docker", "exec", CONTAINER, "node", "-p", "require('/homebridge/node_modules/homebridge-yeelighter/package.json').version"]).strip()
                except Exception as exc:
                    sample["runtime_error"] = str(exc)
                next_version = cycle + 60
            events(reader.readlines(), state, seen)
            samples.write(json.dumps(sample) + "\n")
            samples.flush()
            state["samples"] += 1
            state["last_sample"] = sample
            state["elapsed_seconds"] = round(time.monotonic() - begin)
            save(out / "summary.json", state)
            time.sleep(max(0, 10 - (time.monotonic() - cycle)))
    finally:
        if follower and follower.poll() is None:
            follower.terminate()
            follower.wait(timeout=10)
        events(reader.readlines(), state, seen)
        state["complete"] = not stopped
        state["stopped_early"] = stopped
        state["end"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save(out / "summary.json", state)
        reader.close()
        samples.close()
        log.close()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "run", "status", "stop"])
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--seconds", type=int, default=86400)
    args = parser.parse_args()
    if args.action == "start":
        active = json.loads((ROOT / "active.json").read_text())
        baseline = json.loads((ROOT / "backups" / active["backup"] / "identity.json").read_text())
        parent = ROOT / "observations"
        parent.mkdir(exist_ok=True)
        out = parent / datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        out.mkdir()
        meta = {"start": datetime.datetime.now(datetime.timezone.utc).isoformat(), "duration_seconds": args.seconds, "hosts": baseline["hosts"], "active": active}
        save(out / "run.json", meta)
        with (out / "observer.log").open("w") as log:
            child = subprocess.Popen([sys.executable, __file__, "run", "--directory", str(out), "--seconds", str(args.seconds)], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        meta["pid"] = child.pid
        save(out / "run.json", meta)
        print(json.dumps({"directory": str(out), **meta}))
    else:
        out = args.directory or sorted((ROOT / "observations").iterdir())[-1]
        if args.action == "run":
            observe(out, args.seconds)
        elif args.action == "status":
            print((out / "summary.json").read_text())
        elif args.action == "stop":
            pid = json.loads((out / "run.json").read_text())["pid"]
            commandline = Path("/proc") / str(pid) / "cmdline"
            assert str(out).encode() in commandline.read_bytes() and b"observe.py" in commandline.read_bytes(), "Observer PID does not match"
            os.kill(pid, signal.SIGTERM)


if __name__ == "__main__":
    main()
