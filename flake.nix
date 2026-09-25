{
  description = "Fujifilm raw converter: develop RAFs through the camera's own engine";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    rawji-src = {
      url = "github:pinpox/rawji/02cb559bb1555b3f77e4b246cad06c44cff56c8e";
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
          version =
            (builtins.fromTOML (builtins.readFile ./pyproject.toml))
            .project.version;

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

          lsdetect = python.pkgs.buildPythonPackage {
            pname = "lsdetect";
            version = "0.1.0";
            format = "wheel";
            src = python.pkgs.fetchPypi {
              pname = "lsdetect";
              version = "0.1.0";
              format = "wheel";
              dist = "cp311";
              python = "cp311";
              abi = "abi3";
              platform = "manylinux_2_17_x86_64.manylinux2014_x86_64";
              hash = "sha256-xyy0i4CpbohAC1dIi14x7wBzP4qSbkdT36OMfWW++to=";
            };
            pythonImportsCheck = [ "lsdetect" ];
          };

          # GI typelibs and native libs needed at runtime.
          gtkStack = [
            pkgs.adwaita-icon-theme
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
            inherit version;
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
              lsdetect
              python.pkgs.numpy
              python.pkgs.pygobject3
              python.pkgs.pyusb
              rawji
            ];

            dontWrapGApps = true;

            makeWrapperArgs = [
              "\${gappsWrapperArgs[@]}"
              "--prefix"
              "XDG_DATA_DIRS"
              ":"
              "${pkgs.adwaita-icon-theme}/share"
              "--prefix"
              "PATH"
              ":"
              "${pkgs.libjxl.bin}/bin"
              "--prefix"
              "LD_LIBRARY_PATH"
              ":"
              "${pkgs.libheif}/lib"
            ];

            pythonImportsCheck = [
              "grawji"
              "grawji.camera.camera_info"
              "grawji.imaging.render16"
              "grawji.imaging.tiff"
            ];

            meta = {
              description = "Fujifilm raw converter: develop RAFs through the camera's own engine";
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
