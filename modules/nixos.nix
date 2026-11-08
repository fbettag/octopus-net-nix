{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.octopus-net;
  profiles = lib.mapAttrs (
    name: device:
    pkgs.writeText "octopus-net-${name}.json" (builtins.toJSON (removeAttrs device [ "passwordFile" ]))
  ) cfg.devices;
in
{
  options.services.octopus-net = {
    enable = lib.mkEnableOption "declarative configuration of external Octopus NET Pro tuners";
    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.callPackage ../package.nix { };
      description = "Octopus NET reconciler package.";
    };
    devices = lib.mkOption {
      default = { };
      type = lib.types.attrsOf (
        lib.types.submodule {
          options = {
            host = lib.mkOption { type = lib.types.str; };
            expectedUuid = lib.mkOption { type = lib.types.str; };
            expectedSerial = lib.mkOption { type = lib.types.str; };
            expectedCapability = lib.mkOption { type = lib.types.str; };
            firmwareVersion = lib.mkOption {
              type = lib.types.str;
              description = "Required Pro firmware version; mismatches prevent configuration writes. Firmware is never flashed automatically.";
            };
            username = lib.mkOption {
              type = lib.types.str;
              default = "admin";
            };
            passwordFile = lib.mkOption {
              type = lib.types.str;
              description = "Runtime credential for the appliance; also provisions its initial password after a reset.";
            };
            tuners = lib.mkOption {
              type = lib.types.listOf (lib.types.attrsOf lib.types.str);
              description = "Tuner fields keyed by adapter/frontend, using the Pro management API names. Unspecified fields are preserved.";
            };
          };
        }
      );
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = lib.mapAttrsToList (name: device: {
      assertion =
        builtins.match "[a-zA-Z0-9][a-zA-Z0-9_-]*" name != null
        && lib.hasPrefix "/" device.passwordFile
        && !lib.hasPrefix "/nix/store/" device.passwordFile;
      message = "Octopus NET devices need a safe unit name and an absolute runtime password path outside the Nix store.";
    }) cfg.devices;
    environment.systemPackages = [ cfg.package ];
    environment.etc = lib.mapAttrs' (
      name: _: lib.nameValuePair "octopus-net/${name}.json" { source = profiles.${name}; }
    ) cfg.devices;

    systemd.services = lib.mapAttrs' (
      name: device:
      lib.nameValuePair "octopus-net-${name}" {
        description = "Reconcile Octopus NET ${name} tuner configuration";
        wantedBy = [ "multi-user.target" ];
        wants = [ "network-online.target" ];
        after = [ "network-online.target" ];
        restartTriggers = [ profiles.${name} ];
        serviceConfig = {
          Type = "oneshot";
          ExecStart = "${cfg.package}/bin/octopus-net-nix apply ${profiles.${name}}";
          LoadCredential = "password:${device.passwordFile}";
          StateDirectory = "octopus-net/${name}";
          StateDirectoryMode = "0700";
          TimeoutStartSec = 90;
          UMask = "0077";
          DynamicUser = true;
          NoNewPrivileges = true;
          ProtectSystem = "strict";
          ProtectHome = true;
          PrivateTmp = true;
          RestrictAddressFamilies = [
            "AF_INET"
            "AF_INET6"
          ];
        };
      }
    ) cfg.devices;

    systemd.timers = lib.mapAttrs' (
      name: _:
      lib.nameValuePair "octopus-net-${name}" {
        wantedBy = [ "timers.target" ];
        timerConfig = {
          OnBootSec = "3min";
          OnUnitInactiveSec = "15min";
          RandomizedDelaySec = "30s";
        };
      }
    ) cfg.devices;
  };
}
