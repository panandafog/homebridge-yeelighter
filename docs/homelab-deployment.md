# Lenovo deployment and rollback

Branch: `fix/yeelight-reconnect`. The package name remains
`homebridge-yeelighter`, and the platform remains `Yeelighter`. Local builds
have a version distinct from upstream. Light UUIDs use the same device IDs.

Run these commands from the repository root on the Mac, with Python 3, Node
and pnpm available in PATH:

```sh
python3 scripts/homelab.py build
python3 scripts/homelab.py preflight .homelab/releases/RELEASE_ID
python3 scripts/homelab.py deploy .homelab/releases/RELEASE_ID
python3 scripts/homelab.py status
python3 scripts/homelab.py rollback BACKUP_ID
```

`build` requires a clean commit, runs the build and tests, and creates a
`.tgz` with compiled JavaScript and two pure JavaScript runtime dependencies.
The Mac's complete node_modules directory is not transferred. The commit SHA
and file SHA256 checksums are recorded in `release.json`.

`preflight` installs the exact archive through npm **offline** in a disposable
container using the running Homebridge image. Networking is disabled; tests
use only loopback sockets. Production node_modules are mounted read-only to
provide the installed Homebridge/HAP libraries for tests. The container does
not start Homebridge or connect to real lights.

`deploy` repeats preflight, briefly pauses the production container to take a
consistent snapshot, then resumes it. The backup is downloaded to the Mac and
verified against SHA256 checksums **before** replacing the plugin. Installation
and rollback are then rehearsed on a separate data copy without publishing a
HomeKit bridge. Only after these checks does the script stop production
Homebridge and replace the plugin. Other packages are not reinstalled. The
plugin dependency in `/homebridge/package.json` references a local `.tgz`
stored on the persistent volume.

Startup checks require HTTP 200, connections to the cached lights, the expected
plugin version, and unchanged config.json, pairing keys and clients, accessory
UUIDs, and previously assigned HomeKit IDs. Installation or verification errors
trigger automatic rollback. Verification runs for up to 90 seconds; it does not
continue monitoring after the command finishes.

The backup preserves the exact installed plugin files, including the previous
local patch, and Homebridge state: config.json, persist, accessories,
plugin-persist, UI settings, and other top-level service files. Logs, built-in
UI backups, and other node_modules are not copied again. Other plugins, Node
and the image are unchanged by this process. A separate full original
Homebridge backup is also stored in the Mac's homelab directory.

`rollback` restores the **working build from the snapshot**, its dependencies
and saved Homebridge state, including pairings and the accessory cache. Lights
and the bridge do not need to be removed from or added to Apple Home. If the
user has edited config.json since the snapshot, automatic rollback refuses to
overwrite those changes; the differences need to be reviewed. Wi-Fi outages
can prevent connectivity checks from passing even after files have been
restored correctly.

Backups are private (directories 700, files 600) and are not committed. Defaults:

- Mac: `~/Software/homelab/backups/homebridge/before-BACKUP_ID/`;
- Lenovo: `/home/panandafog/Software/homebridge-homelab/backups/`;
- the local backup includes `ROLLBACK.txt` with a ready-to-run SSH command.

Lenovo currently runs Node 20.12.2, below the plugin's declared minimum for
Node 20. Preflight deliberately tests this existing runtime; Node/image
upgrades are handled separately from this deployment.

For 24-hour observation on Lenovo, use `observe.py` next to the controller:

```sh
python3 observe.py start --seconds 86400
python3 observe.py status
python3 observe.py stop
```

The observer saves private Docker logs, local socket/HTTP checks every
10 seconds, disconnect counts and recovery durations. It creates no direct
connections to lights and sends them no commands. It exits after 24 hours;
observation results do not trigger notifications or automatic rollback.
`status` reads the collected data even after the process has finished.
