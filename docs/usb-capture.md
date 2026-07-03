# Capturing X Raw Studio's USB traffic

How the verified profile offsets grawji writes were found: let Fuji's
own X Raw Studio drive the camera, capture the USB traffic on a Linux
host, and diff the d185 profile writes while toggling exactly one
setting. This is the ground truth when rawji's labels and the camera's
behaviour disagree - a slot found this way still needs a hardware render
check afterwards (`scripts/verify_offsets.py`).

## Setup

X Raw Studio runs in a Windows guest under QEMU/KVM (virt-manager),
with the camera passed through to the guest so every byte crosses the
host's USB monitoring interface:

1. Put the camera in "USB RAW CONV./BACKUP RESTORE" mode and connect it.
2. In virt-manager, add the Fujifilm device (vendor id 04cb) as a USB
   host device of the Windows guest. Install X Raw Studio in the guest.
3. On the host, load the capture module and find the camera's bus:

   ```sh
   sudo modprobe usbmon
   lsusb | grep 04cb        # note the bus number N
   sudo tcpdump -i usbmonN -w xrs.pcap
   ```

## Capture procedure

One capture per question. With tcpdump running:

1. Open a RAF from the connected body in X Raw Studio.
2. Change exactly one setting between two values (say Clarity -5, then
   +5), triggering a conversion for each.
3. Stop the capture.

Keeping the toggle to a single setting is what makes the diff readable.

## Analysis

Open the pcap in Wireshark and look at the bulk OUT transfers to the
camera's device address. X Raw Studio uploads the profile as PTP
property 0xd185; the payload is easy to spot by its size (601, 605 or
629 bytes depending on the body - it starts with a u16 parameter count
followed by the length-prefixed wide-char IOPCode).

Extract the profile payload sent before and after the toggle and diff
them byte-wise. Exactly one 4-byte little-endian slot should differ;
its offset is `513 + index * 4`. If more than one slot moved, the
capture mixed edits - redo it. The value pair tells you the encoding
(e.g. Clarity -5/+5 showed -50/+50, so the slot encodes value*10).

petabyt's [fp](https://github.com/petabyt/fp) parser documents many
field encodings and is the best cross-reference for a slot you cannot
identify.

## From finding to merge

A capture identifies the slot. It does not prove the camera honours it
over grawji's write path. Confirm with a render A/B via
`scripts/verify_offsets.py` (byte-identical renders mean the value is
ignored), then update `grawji/capabilities.py` and
`docs/feature-matrix.md` together, recording the body and date.
