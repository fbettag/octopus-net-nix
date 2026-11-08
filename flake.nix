{
  description = "Declarative Octopus NET Pro management for NixOS";
  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "aarch64-darwin"
        "aarch64-linux"
        "x86_64-linux"
      ];
      forSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      packages = forSystems (system: {
        default = nixpkgs.legacyPackages.${system}.callPackage ./package.nix { };
      });
      apps = forSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/octopus-net-nix";
        };
      });
      formatter = forSystems (system: nixpkgs.legacyPackages.${system}.nixfmt-tree);
      nixosModules.default = import ./modules/nixos.nix;
      checks = forSystems (system: {
        package = self.packages.${system}.default;
        module =
          let
            pkgs = nixpkgs.legacyPackages.${system};
            linux = nixpkgs.lib.nixosSystem {
              system = "x86_64-linux";
              modules = [
                self.nixosModules.default
                {
                  services.octopus-net = {
                    enable = true;
                    devices.test = {
                      host = "192.0.2.1";
                      expectedUuid = "00000000-0000-4000-8000-000000000001";
                      expectedSerial = "EXAMPLE-001";
                      expectedCapability = "DVBS2-2";
                      firmwareVersion = "octonet-pro-2.3.1";
                      passwordFile = "/run/secrets/octopus-password";
                      tuners = [
                        {
                          adapter = "0";
                          frontend = "0";
                          enable = "true";
                        }
                      ];
                    };
                  };
                }
              ];
            };
            service = linux.config.systemd.services.octopus-net-test.serviceConfig;
          in
          assert service.Type == "oneshot";
          assert service.LoadCredential == "password:/run/secrets/octopus-password";
          assert service.DynamicUser;
          assert linux.config.systemd.timers.octopus-net-test.timerConfig.OnUnitInactiveSec == "15min";
          pkgs.runCommand "octopus-net-module" { } "touch $out";
      });
    };
}
