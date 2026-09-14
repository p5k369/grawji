# Help enable bank writing on your camera

grawji can write recipes into the camera's custom banks (C1..Cn) over
USB, but every body stores them differently, and mapping a body safely
needs data from a real camera. Here you find an explanation of what to
capture and send so that your body can be supported. Everything below
is read-only: nothing here writes to your camera.

Open a
[camera body report](https://github.com/p5k369/grawji/issues/new?template=new-body-report.yml)
with the outputs described for your case. Please read the warranty
section in the README first: connecting any non-licensed program to the
camera may affect your warranty, and that includes these probes.

The scripts in cases 2 and 3 run from a source checkout (setup in
[CONTRIBUTING.md](../CONTRIBUTING.md)) and need no dependencies beyond
grawji's own.

## Which case is yours?

| Your body | Case                               |
|-----------|------------------------------------|
| X-Pro2, X-T2, X-T20, X-E3, X-T30 | Case 1: expected to work, untested |
| X-Processor 5 body other than the X-E5 | Case 1: expected to work, untested |
| X-Processor 5 body where the transfer errors | Case 2: property dump              |
| X-H1, X-Pro3, X100V, X-T4, X-S10, X-E4, X-T30 II | Case 3: layout mapping             |
| GFX 50S, 50R, 100, 100S, 50S II | Case 3: layout mapping             |

## Case 1: untested bodies that should work

Your body shares its write mechanism with a verified one, and grawji's
guards refuse to write anything unexpected. Just try it: put the camera
in USB RAW CONV./BACKUP RESTORE mode, open Manage Recipes and drag a
recipe onto a bank. Then report your camera model, whether the bank
shows the recipe correctly on the camera afterwards, and, if it failed,
the exact error text.

## Case 2: X-Processor 5 body where the transfer errors

These bodies expose the banks as USB properties, and grawji reads which
ones your body advertises. Run:

    python scripts/probe_preset_props.py caps
    python scripts/probe_preset_props.py list

and attach both outputs to the issue. `list` prints your bank names and
their stored settings, so check the output for anything you would
rather not share before posting.

## Case 3: bodies without a mapped layout

These bodies keep the banks inside the camera's settings backup, and
each model lays that file out differently. The layout is found by
downloading the backup, changing exactly one setting on the camera, and
downloading again: the changed bytes reveal where that setting lives.

**Do not send the downloaded `.bin` files themselves. They contain your
camera's serial number in cleartext.** Send only the `diff` outputs and
the file size.

1. Put the camera in USB RAW CONV./BACKUP RESTORE mode.
2. Download a baseline:

        python scripts/probe_backup.py download baseline.bin

3. On the camera, change exactly ONE thing in ONE custom bank, for
   example set the film simulation of C2 to Velvia (Edit/Save Custom
   Setting in the IQ menu).
4. Download again and diff:

        python scripts/probe_backup.py download step1.bin
        python scripts/probe_backup.py diff baseline.bin step1.bin

5. Repeat steps 3 and 4 for EVERY field your body's banks store. A
   complete layout needs all of them: film simulation, white balance
   mode, white balance color temperature, dynamic range, highlight
   tone, shadow tone, color, sharpness, noise reduction, grain effect,
   and, where your body has them, grain size, Color Chrome Effect,
   Color Chrome FX Blue, Clarity, Smooth Skin and Monochromatic Color.
   Also rename one bank if your body supports bank names. Note down
   what you changed to what before each download.
6. For fields with few choices, capture EVERY value once (grain,
   Color Chrome, dynamic range). For the color field this matters
   most: capture all nine values from -4 to +4, because on the
   verified bodies its codes follow no formula. For sliders like the
   tones, two or three values are enough to reveal the encoding.
7. Change the SAME field in two DIFFERENT banks once (for example the
   film simulation of C1 and of C3): the distance between the two
   changed offsets reveals the size of one bank record.
8. Post the diff outputs, the blob size, your camera model and firmware
   version, and your change notes.

That is a few dozen downloads in total. Two shortcuts make it quick:
you can set several DIFFERENT banks in one menu session (C1 film sim,
C2 white balance, ...) and capture them in a single diff, as long as
your notes say which bank got which change. And a partial capture is
still welcome: post what you have and the remaining rounds can be
worked out together in the issue.

Practical notes:

- Downloading is read-only and cannot change anything on the camera.
- Do not run the `restore` command. It writes persistent camera
  settings and is only for maintainers with a mapped layout.
- If a USB operation times out, power-cycle the camera instead of
  retrying.
- Some bodies drop USB when you use the camera menus while connected.
  Change the setting first, then plug in and download promptly.

With those diffs the bank offsets, the value encodings and any
checksum the body validates can be solved, and grawji should be able
to support your body with the next release.
