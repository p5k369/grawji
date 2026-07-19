{
  description = "GTK4 frontend for rawji";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    rawji-src = {
      url = "github:pinpox/rawji";
      flake = false;
    };
  };

  outputs =
    { nixpkgs, rawji-src, ... }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;

      packagesFor =
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          python = pkgs.python3;

          rawji = python.pkgs.buildPythonPackage {
            pname = "rawji";
            version = "0.1.0";
            pyproject = true;
            src = rawji-src;

            build-system = with python.pkgs; [
              setuptools
              wheel
            ];
            dependencies = [ python.pkgs.pyusb ];

            pythonImportsCheck = [ "rawji" ];
          };

          # GI typelibs and native libs needed at runtime.
          gtkStack = [
            pkgs.gtk4
            pkgs.libadwaita
            pkgs.gexiv2
            pkgs.gdk-pixbuf
            pkgs.graphene
            pkgs.pango
            pkgs.glib
          ];

          grawji = python.pkgs.buildPythonApplication {
            pname = "grawji";
            version = "0.2.0";
            pyproject = true;
            src = ./.;

            strictDeps = true;

            build-system = [ python.pkgs.hatchling ];

            nativeBuildInputs = [
              pkgs.gobject-introspection
              pkgs.wrapGAppsHook4
            ];

            buildInputs = gtkStack;

            dependencies = [
              python.pkgs.pygobject3
              python.pkgs.pyusb
              rawji
            ];

            dontWrapGApps = true;

            makeWrapperArgs = [ "\${gappsWrapperArgs[@]}" ];

            pythonImportsCheck = [ "grawji" ];

            meta = {
              description = "GTK4 frontend for rawji - interactive Fuji RAF conversion via the camera engine";
              homepage = "https://github.com/p5k369/grawji";
              license = pkgs.lib.licenses.gpl3Plus;
              mainProgram = "grawji";
              platforms = pkgs.lib.platforms.linux;
            };
          };
        in
        {
          default = grawji;
          grawji = grawji;
          rawji = rawji;
        };
    in
    {
      packages = forAllSystems packagesFor;
    };
}
