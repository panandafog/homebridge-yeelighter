<h1 align="center">Yeelighter Panandafog</h1>
<p>
  <a href="https://www.npmjs.com/package/homebridge-yeelighter-panandafog" target="_blank">
    <img alt="Version" src="https://img.shields.io/npm/v/homebridge-yeelighter-panandafog.svg">
  </a>
  <a href="LICENSE">
    <img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-yellow.svg" />
  </a>
</p>

This is [panandafog's fork](https://github.com/panandafog/homebridge-yeelighter)
of [homebridge-yeelighter](https://github.com/cellcortex/homebridge-yeelighter),
originally developed by Thomas Kroeber (@cellcortex), under the MIT license.

The `dev/panandafog` branch starts from upstream **2.16.1** and currently contains
only the fork's package identity and documentation changes. Reconnection fixes
are being tested separately on `fix/yeelight-reconnect` and are not included here.
The development package is **not yet published to npm**.

Yeelight support for Homebridge: https://github.com/nfarina/homebridge with particular focus on supporting the special features of ceiling lights.

There are many plugins for Yeelight already. This one is unique (so far) in supporting the
background light that some yeelights have and also has a diffent approach to the moonlight mode (exposed as just another range for brightness).

If a light supports a background light, it will show up as a secondary service in the light accessory. If a light supports moonlight mode, the brightness will be adjusted so that the lower 50% are reserved for moonlight brightness and the upper 50% are using the "normal" mode. While this makes it simple to control the moonlight mode, it has the small drawback that setting the color-temperature will only work when in the normal light mode. I could not find an API to set the color temperature of the moonlight.

### 🏠 [Homepage](https://github.com/panandafog/homebridge-yeelighter)

## Prerequisites

- Node.js matching `^16.14.2 || ^18.20.4 || ^20.15.1 || >22.3.0` (package engine declaration).
- Homebridge matching `^1.6.0 || ^2.0.0-beta.0`.

## Installation

After the first npm release, install `homebridge-yeelighter-panandafog` using
Homebridge UI's Plugins tab, or your installation's usual npm plugin workflow.
For a development release, build and inspect the package before installing its `.tgz`.

The npm package is renamed, while the configuration platform remains `Yeelighter`.
When migrating an existing installation, back up `config.json`, `persist` and
`accessories`, replace the original plugin with the fork, and verify cached
accessory restoration before regular use. Keep the existing bridge identity and
device UUIDs. Migration to the renamed package has not yet been tested on Lenovo.

## Configuration

In Homebridge's config.json, configure the `Yeelighter` platform provided by
`homebridge-yeelighter-panandafog`. The minimal configuration enables discovered lights:

```
"platforms": [
  {
    "platform": "Yeelighter",
    "name": "Yeelighter Panandafog",
    "ctforcolor": true,
    "timeout": 5000,
    "interval": 60000
  }
]
```

The plugin supports setting the configuration through [homebridge-config-ui-x](https://github.com/oznu/homebridge-config-ui-x).

Set `ctforcolor` to `true` to enable white color temperature controls for RGB lights that support them.
When editing JSON directly, include this option explicitly; the settings UI's default is not applied to omitted JSON fields.

If rapid slider or swatch changes produce `client quota exceeded` in the logs, add `"animateChanges": 500`
to the platform configuration. This enables the existing debounce and smooth transitions, combining repeated
changes to the same command during the default 500 ms window. It reduces command traffic but does not enforce
a total command rate limit. Restart Homebridge after changing these settings.

You can use the override array to override the automatic configuration of the lights. An example for disabling the light with id `0x00000000deadbeef` and enabling moonlight and disabling background for `0x0000000012345678`:

```
"platforms": [
  {
    "platform": "Yeelighter",
    "name": "Yeelighter Panandafog",
    "override": [
      {
        "id": "0x00000000deadbeef",
        "ignored": true
      },
      {
        "id": "0x0000000012345678",
        "background": false,
        "nightLight": true
      }
    ]
  }
]
```

### Optional per light configuration:

- `id: string` (mandatory) - the id of the light (as reported in the device config of homebridge)
- `name?: string` (optional) - the name to be used in log messages
- `color?: boolean` (optional) - this light is full RGB color
- `backgroundLight?: boolean` (optional) - this light has a secondary color mood light
- `nightLight?: boolean` (optional) - this light supports moonlight mode
- `log?: boolean` (optional) - enalbe detailed logging for this light
- `offOnDisconnect?: boolean` (optional) - switch the light off in homekit when it disconnects (this is for people with old-school wall switches)
- `colorTemperature?: { min: number, max: number }` (optional) - colorTemperature limits in kelvin

### Forcing a light to be shown (for network configs that don't support multicast)

Since the yeelights respond with their configuration to the discovery request, this information
needs to be manually entered into the config. Use at your own risk - this may be screwing with
your config since there's no way for Yeelighter to tell if the light is really there.

- `id: string` - the id of the light (you have to find this from the app)
- `address: string` - the IP address of the light
- `model: string` - model (name of the light)
- `supports: string` - whitespace separated list of commands that the light supports.

## Setting up the lights

Make sure to enable "LAN control" for the lights you want to control. This is done in the Yeelight app either when setting up the light or in the lights settings which hide behind the ⏏ - button.

There are existing configurations for a number of lights. If your light is not supported, it will
hopefully fall back to a decent fallback configuration. You can use the settings to fine-tune it. In those cases, it would be great if you could open a ticket in the [issues page](https://github.com/panandafog/homebridge-yeelighter/issues) including the homebridge logs for the light being setup so
the configuration can be added for future users.

## Original author

👤 **Thomas Kroeber**

- Github: [@cellcortex](https://github.com/cellcortex)

Fork maintained by [@panandafog](https://github.com/panandafog).
The original copyright and MIT license are retained in [LICENSE](LICENSE).

## 🤝 Contributing

Contributions, issues and feature requests are welcome!<br />Feel free to check [issues page](https://github.com/panandafog/homebridge-yeelighter/issues).

The `release*` scripts only prepare local versions and changelogs. They do not
push branches or publish packages. Publication is a separate, explicit step.

## Show your support

Give a ⭐️ if this project helped you!
