# spa-pool

Home Assistant custom integration for a Balboa-compatible spa pool connected through an Elfin Wi-Fi RS-485-to-TCP adapter.

The integration communicates directly with the spa controller over TCP on the local network. It does not require a cloud service, the identification/configuration handshake used by the official Balboa Wi-Fi module, or any intermediary software such as a Ruby proxy or MQTT broker.

> [!WARNING]
> This project has been developed and tested against a specific Balboa-compatible spa installation. Balboa controller configurations and message layouts vary. Confirm operation carefully before relying on it for unattended control.

## Features

- Local, asynchronous TCP communication with the spa controller
- Home Assistant UI configuration flow
- Automatic reconnection after network or bridge interruptions
- Optional Elfin EW11 software restart from Home Assistant
- Current and target water temperature
- Heating state and heat mode
- Ready, Rest and Ready-in-Rest control
- High and low temperature-range control
- Spa clock display and clock synchronisation
- Filter-cycle, lock, circulation-pump and controller-state sensors
- Maintenance reminder/notification decoding and clearing
- Fault-log retrieval through a Home Assistant event entity
- Stateless controls for pumps, blowers and lights
- Diagnostic entities for protocol investigation
- Downloadable Home Assistant diagnostics with connection and protocol statistics

## Hardware

The expected topology is:

```text
Home Assistant
      |
   Wi-Fi/LAN
      |
Elfin RS-485-to-TCP adapter
      |
    RS-485
      |
Balboa-compatible spa controller
```

The integration was developed with a transparent Elfin-style adapter, such as an **Elfin EW11**, replacing or supplementing the official Balboa Wi-Fi interface.

Typical bridge settings are:

| Setting | Value |
|---|---|
| Mode | Transparent TCP server |
| TCP port | `4257` |
| Serial baud rate | `115200` |
| Data bits | `8` |
| Parity | None |
| Stop bits | `1` |
| Flow control | None |

The exact Elfin configuration interface varies by model and firmware. The adapter must expose the raw spa bus as an unmodified TCP byte stream.

> [!CAUTION]
> The spa controller is mains-powered equipment. Use an appropriately isolated RS-485 interface and follow the controller and adapter manufacturers' electrical requirements. Do not work on the spa wiring while it is energised.

## Installation

### Manual installation

1. Copy the integration directory into Home Assistant:

   ```text
   /config/custom_components/spa_pool/
   ```

2. Confirm that the directory contains at least:

   ```text
   custom_components/
   └── spa_pool/
       ├── __init__.py
       ├── binary_sensor.py
       ├── button.py
       ├── climate.py
       ├── client.py
       ├── config_flow.py
       ├── const.py
       ├── diagnostics.py
       ├── elfin.py
       ├── event.py
       ├── manifest.json
       ├── models.py
       ├── protocol.py
       ├── sensor.py
       ├── strings.json
       └── translations/
           └── en.json
   ```

3. Restart Home Assistant.

4. Go to **Settings → Devices & services → Add integration**.

5. Search for **Spa Pool**.

6. Enter the Elfin adapter's IP address or hostname and TCP port. The default port is `4257`.

The setup flow verifies that the bridge can be reached and that at least one valid spa frame can be received.

### HACS custom repository

The repository includes HACS metadata. Once the project has been published on GitHub:

1. Open **HACS → Integrations**.
2. Select the three-dot menu and choose **Custom repositories**.
3. Add the GitHub repository URL as an **Integration**.
4. Install **Spa Pool** and restart Home Assistant.
5. Add the integration from **Settings → Devices & services**.

## Entities

The exact entity set depends on the integration version and the configured spa capabilities.

### Climate

The main climate entity provides:

- Current water temperature
- Target temperature
- Heating state
- Heat mode/preset selection
- Temperature-range selection

Example service calls:

```yaml
action: climate.set_temperature
target:
  entity_id: climate.spa_pool
data:
  temperature: 38
```

```yaml
action: climate.set_preset_mode
target:
  entity_id: climate.spa_pool
data:
  preset_mode: ready
```

### Accessory controls

Pumps, blowers and lights are controlled using stateless **next state** or **next mode** buttons.

A button press is equivalent to pressing the corresponding physical spa-panel button. Depending on the installed accessory, repeated presses may cycle through states such as:

```text
Off → Low → High → Off
```

or:

```text
Off → Mode 1 → Mode 2 → … → Off
```

This approach avoids falsely asserting an accessory state when the controller's status-byte mapping has not been verified for a particular spa configuration.

Example:

```yaml
action: button.press
target:
  entity_id: button.spa_pool_pump_2_next_state
```

### Sensors and binary sensors

Available entities include operational and diagnostic information such as:

- Current and target temperature
- Spa time
- Operational state
- Initialisation/controller-notification state
- Heat mode and temperature range
- Heating active
- Filter-cycle state
- Circulation pump active
- Panel and settings locks
- Maintenance notification
- Status-stream availability
- Raw or last-valid protocol messages

Some protocol-oriented entities are disabled by default and can be enabled from the integration's entity list.

### Buttons

Management and diagnostic actions include:

- Restart stream
- Restart Elfin bridge (disabled by default)
- Synchronise clock
- Refresh fault log
- Refresh device configuration
- Clear controller notification
- Advance a pump, blower or light to its next state

The **Restart Elfin bridge** button sends the same local management request as the EW11 web interface's Restart control (`CID 20003`). It is separate from **Restart stream**, which only rebuilds Home Assistant's TCP connection. The current implementation uses the stock EW11 HTTP Basic credentials `admin` / `admin` and is disabled by default so installations using other bridge hardware are unaffected.

### Fault-log event

The fault-log event entity reports decoded controller fault information when a fault entry is received. Event attributes can include the message code, description, severity, controller time and age of the stored entry.

Example automation:

```yaml
alias: Spa fault notification
triggers:
  - trigger: state
    entity_id: event.spa_pool_fault_log
conditions:
  - condition: template
    value_template: >
      {{ trigger.to_state is not none
         and trigger.to_state.attributes.get('message_code', 0) | int(0) != 0 }}
actions:
  - action: persistent_notification.create
    data:
      title: Spa fault
      message: >
        {{ trigger.to_state.attributes.get(
             'description', 'Unknown spa fault'
           ) }}
mode: queued
```

## Protocol behaviour

Balboa-compatible messages use `0x7E` frame delimiters and a one-byte checksum. The integration:

1. Reassembles frames from arbitrary TCP chunks.
2. Validates declared lengths and checksums.
3. Decodes supported status, configuration, maintenance and fault messages.
4. Preserves unknown values rather than terminating the stream.
5. Serialises outgoing commands so that integration commands do not interleave.
6. Retains raw diagnostic data for investigation of unsupported controllers.

The bridge is treated as a transparent transport. The integration does not assume that it emulates all behaviour of an official Balboa Wi-Fi module.

## Troubleshooting

### Integration cannot connect

Check that:

- The Elfin adapter has a stable IP address or DHCP reservation.
- TCP port `4257` is reachable from Home Assistant.
- The adapter is operating as a transparent TCP server.
- Serial settings match the spa bus.
- Another application is not occupying the bridge's only permitted TCP connection.
- Home Assistant can receive continuous spa traffic after connecting.

### Integration connects but no valid status is received

Verify the serial wiring, polarity and bridge settings. A TCP connection alone does not prove that valid RS-485 data is reaching Home Assistant.

If an EW11 remains reachable on the network and port `4257` accepts a TCP connection but the incoming data is no longer valid `0x7E`-framed spa traffic, enable the diagnostic **Restart Elfin bridge** button and restart the adapter. This performs a software restart of the EW11 without factory-resetting its configuration. The normal background reconnect loop should reconnect to the spa stream once the adapter is back online.

Enable the diagnostic entities and inspect the integration logs:

```yaml
logger:
  default: warning
  logs:
    custom_components.spa_pool: debug
```

Restart Home Assistant after changing logger configuration.

### A pump, blower or light cycles incorrectly

Accessory numbering and state encoding can differ between controller configurations. Use the corresponding **next state** button cautiously and compare each press with the physical control panel and the raw status frame.

### Downloading diagnostics

Go to:

**Settings → Devices & services → Spa Pool → three-dot menu → Download diagnostics**

Diagnostics include connection state, parser statistics, decoded controller state and recent protocol information. Review diagnostics before sharing them, even though Home Assistant's diagnostics framework is intended to redact configured secrets.

## Development status

This is an independently developed custom integration and is not an official Home Assistant or Balboa Water Group project.

The protocol has been reverse engineered from observed traffic and publicly available community implementations. Not every Balboa controller, topside panel, accessory configuration or firmware version is expected to behave identically.

Useful contributions include:

- Packet captures paired with a description of the physical panel action
- Controller and topside-panel model information
- Previously unseen status or fault messages
- Tests for protocol parsing and command generation
- Confirmed accessory-state mappings for additional spa configurations

Please do not include public IP addresses, Wi-Fi credentials or other private network information in issues or captures.

## Related projects and references

This project was developed independently but benefited from the protocol research, documentation, and prior work of the Home Assistant and Balboa communities, particularly the following projects:

- **ccutrer/balboa_worldwide_app** – extensive reverse engineering of the Balboa spa protocol, including protocol documentation and RS-485/TCP behaviour.
- **garbled1/pybalboa** – asynchronous Python library for communicating with Balboa spa controllers, used by several other community projects.
- **garbled1/balboa_homeassistan** – Home Assistant integration designed for use with the official Balboa Wi-Fi module.
- **jshank/bwalink** – demonstrates communication with Balboa controllers via generic RS-485-to-TCP adapters, including the Elfin EW11.
- **Home Assistant Core** – integration architecture, entity-platform patterns, and developer APIs.
- **HACS** – framework and validation requirements for distributing Home Assistant custom integrations.

Unlike many existing solutions, **Spa Pool** communicates directly with the spa controller over a transparent TCP-to-RS-485 adapter. It does **not** require the official Balboa Wi-Fi module, a cloud service, a Ruby proxy, an MQTT broker, or any other intermediary software.

These projects are acknowledged as valuable references and prior art but are **not** runtime dependencies of this integration.

## Licence

Licensed under the Apache License 2.0. See [LICENSE](LICENSE).
