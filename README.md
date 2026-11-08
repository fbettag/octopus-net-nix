# octopus-net-nix

Declarative tuner configuration for external Digital Devices Octopus NET Pro
SAT>IP receivers, packaged as a Nix flake and NixOS module.

The reconciler checks the receiver's UUID, serial number and SAT>IP capability,
logs in with a runtime credential, and requires an exact Pro firmware version
before changing tuner settings. It preserves fields you do not declare, backs
up the previous settings, writes only when needed, and reads the settings back.
It never flashes firmware or requests a reboot.

## NixOS

Add the flake input and import its module into your host configuration:

```nix
{
  inputs.octopus-net-nix.url = "github:fbettag/octopus-net-nix";
  inputs.octopus-net-nix.inputs.nixpkgs.follows = "nixpkgs";
  # Host modules: [ inputs.octopus-net-nix.nixosModules.default ]
}
```

```nix
services.octopus-net = {
  enable = true;
  devices.receiver = {
    host = "receiver.example.net";
    expectedUuid = "00000000-0000-4000-8000-000000000001";
    expectedSerial = "EXAMPLE-001";
    expectedCapability = "DVBS2-2";
    firmwareVersion = "octonet-pro-2.3.1";
    passwordFile = config.sops.secrets.octopus-password.path;
    tuners = builtins.map (frontend: {
      adapter = "0";
      inherit frontend;
      enable = "true";
      scif_mode = "0";
      scif_freq = "0";
      scif_slot = "0";
      lnb_switch_offset = "11700";
      lnb_low_offset = "9750";
      lnb_high_offset = "10600";
    }) [ "0" "1" ];
  };
};
```

Replace the example identity with your receiver's values from
`http://<receiver>/octoserve/octonet.xml`. The declared adapter/frontend pairs
must match the complete set returned by the device. Tuner fields use the Pro
management API's names and string values. This example describes a universal
LNB without Unicable; adapt it to your installation.

`octopus-net-receiver.service` runs at boot and after profile changes. Its timer
also checks every 15 minutes. The unit uses a dynamic user, systemd credentials,
a private state directory and a restrictive umask. Failed runs are visible in
`journalctl -u octopus-net-receiver`; the next timer run retries.

## Credentials and state

`passwordFile` must be an absolute string path to a runtime password file outside
the Nix store. Use sops-nix or your existing secret provisioning mechanism. Do
not use a Nix path literal or `builtins.readFile` for plaintext passwords.
For sops-nix, configure `restartUnits = [ "octopus-net-receiver.service" ]` on
the secret so a credential change triggers reconciliation.

Public profiles are installed under `/etc/octopus-net/<name>.json`; they contain
the device identity and desired tuner fields, but no password or password path.
Keep real profiles out of public repositories. Backups stay under
`/var/lib/octopus-net/<name>/`, with directory mode `0700` and file mode `0600`.
They include unmanaged receiver settings and must also remain private.

On a receiver requesting its initial admin password, `apply` provisions the
runtime password after verifying device identity. This happens before the
authenticated firmware check. `check` refuses initial password provisioning.
The module does not rotate an existing password.

## CLI

The CLI runs on Linux and macOS; the service module is NixOS-only. A JSON profile
contains the same device fields as above, excluding `passwordFile`. Include
`"username": "admin"` when writing a profile by hand.

```sh
nix run . -- check profile.json --password-file /run/secrets/octopus-password
nix run . -- apply profile.json --password-file /run/secrets/octopus-password --state-dir ./private-state
```

Under systemd, the password defaults to `$CREDENTIALS_DIRECTORY/password` and
the backup directory defaults to `$STATE_DIRECTORY`. `check` authenticates and
compares without changing tuner settings: it exits `0` when matching, `2` for
drift and `1` for operational errors. `apply` exits `0` only when matching or
after successful read-back, and `1` on error. Logs report changed field names
without printing their values, passwords or device serial numbers.

## Scope and limitations

This targets the Octopus NET **Pro** management interface and was extracted from
an existing NixOS setup. Other firmware families are not supported. The firmware
guard prevents tuner writes against an unexpected version; it does not provide
a firmware updater or an atomic rollback. Verification reads back the management
interface and does not prove persistence across a power cycle or RF reception.

The receiver uses HTTP and a cookie-based login. Keep management traffic on a
trusted network. Device identity checks are a wrong-target guard, not
cryptographic authentication. Use one controller per receiver: the management
API does not provide a configuration lock. Concurrent changes can be overwritten.
The module does not manage TVHeadend, Jellyfin, channel lists or conditional access.

## Development

Run `nix fmt` and `nix flake check`. Checks build the CLI, run isolated Python
tests with synthetic devices, and evaluate the NixOS service and timer. No live
receiver or credentials are needed.

Licensed under GPL-3.0-only.
