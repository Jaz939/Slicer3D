# Milestone 1 test print

Print the test plate once on each printer. It holds a 10 mm cube, a 10 mm
cylinder, a 10 mm cone and a flat "L" for checking orientation, all flat on
the build plate with no supports (about 2.6 ml of resin).

| Printer | File | Layers | Est. time |
|---|---|---|---|
| Sonic Mini 8K | `test_plate_sonic-mini-8k.ctb` | 200 x 0.05 mm | ~49 min |
| M5s Pro | `test_plate_photon-mono-m5s-pro.m5sp` | 100 x 0.1 mm | ~12 min |

## Before printing

- Copy the file to a USB stick. The printer should list it with a preview
  showing four shapes. If the printer refuses the file, note the exact
  message (or take a photo) and stop there.
- Exposure is the same as your sample files (Sonic: 2.9 s, bottom 35 s;
  M5s Pro: 1.9 s, bottom 10 s). If you now use a different resin, change it
  with `--exposure` and `--bottom-exposure`.

## What to check

1. **The printer accepts the file** and shows the preview, layer count and time.
2. **The parts stick to the plate** and come off with clean shapes.
3. **Sizes**: cube 10 x 10 x 10 mm, cylinder 10 mm across, cone 10 mm across
   the base. Within about 0.1 to 0.2 mm is normal for resin; the first layers
   are usually a little wider ("elephant foot").
4. **Orientation**: look down on the build plate with the printer facing
   you. The L should have its corner at the bottom left, one arm pointing
   right and one pointing away from you (as on the preview). If it is
   mirrored, tell me which way, and I will flip that printer's profile.
