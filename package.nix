{ lib, python3Packages }:
python3Packages.buildPythonApplication {
  pname = "octopus-net-nix";
  version = "0.1.0";
  pyproject = true;
  src = lib.cleanSource ./.;
  build-system = [ python3Packages.setuptools ];
  nativeCheckInputs = [ python3Packages.pytestCheckHook ];
  pythonImportsCheck = [ "octopus_net" ];
  meta = {
    description = "Declarative Octopus NET Pro tuner configuration";
    license = lib.licenses.gpl3Only;
    mainProgram = "octopus-net-nix";
    platforms = lib.platforms.unix;
  };
}
