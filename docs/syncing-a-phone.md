# Syncing music to a phone

Aeris copies music to a phone by treating it as a folder. That takes one piece of
setup the first time, then it is four clicks.

## Once: make the phone appear as a folder

A phone plugged in over USB speaks **MTP**, which is not a filesystem. On KDE,
Dolphin can browse it anyway because KDE talks MTP itself — but that only works
inside KDE. Every other program, Aeris included, needs the **gvfs** bridge:

```bash
sudo pacman -S gvfs gvfs-mtp
```

On Arch there is no separate `gvfs-fuse` package — the FUSE daemon that does the
translating lives inside `gvfs` itself.

<sub>Debian/Ubuntu: `sudo apt install gvfs-backends gvfs-fuse` · Fedora:
`sudo dnf install gvfs-mtp gvfs-fuse`</sub>

Log out and back in afterwards, so the new services start.

If you skip this step, Aeris will tell you: the Sync tab names the phone it can
see and prints the command for your distribution.

## Every time: connect the phone

1. Plug it in.
2. On the phone, set the USB mode to **File transfer** (or *MTP*). "Charging
   only" is the default on most handsets and hides the storage completely.
3. Open the phone once in your file manager. That is what mounts it — until
   something opens it, there is no folder for Aeris to find.

## Set up the destination

In Aeris: **Music** → **Sync** → **Add a destination**.

| Field | What to put |
| --- | --- |
| **Name** | Anything — "Phone" |
| **Detected** | Pick the phone. This fills in the folder for you |
| **Folder** | Where music goes on the phone, e.g. `…/Internal storage/Music` |
| **Convert to** | `Opus` at 128k is a good default — see below |
| **What to send** | Liked songs, Everything, a playlist, or one artist |
| **Folder layout** | *Same folders as my library* keeps your structure |
| **Album art** | *Embed it, and copy cover.jpg too* suits every player |
| **Lyrics** | *Copy .lrc files* is the safest; embedding also works |
| **Delete files…** | Tick to keep the phone as an exact mirror |

If the phone is not in **Detected**, press **Look again** — mounting can take a
moment. Otherwise use **Browse…** and find it by hand.

Press **Save**.

### Why convert

A FLAC library will not fit on most phones, and no phone benefits from lossless
over earbuds. Converting to Opus at 128k typically cuts the size by
80–90 % with no audible difference. Tags and track numbers come across intact.

**The converted files are never kept on your computer.** Conversion goes through
a scratch file that is deleted the moment it has been handed to the phone, so
you are never left holding a second, lossy copy of your library.

Choose *Keep the original file* if you would rather send the files untouched.

### Artwork and lyrics

Album art is embedded into each file where the format allows it. Opus and Ogg
cannot carry embedded pictures, so for those Aeris copies a `cover.jpg` next to
the tracks instead — which is what phone players read anyway. Leaving **Album
art** on *Embed it, and copy cover.jpg too* covers both cases.

Lyrics embed into every format. If your player prefers separate files, *Copy .lrc
files* puts one beside each track, named to match.

## Sync

Press **What would happen?** first. It reports exactly what the sync will do —
how many tracks would be sent, how many are already there, and what would be
deleted — without touching anything.

Then press **Sync now**. If anything is due to be deleted, Aeris asks before doing
it, and only ever removes audio files inside the destination folder.

Later syncs only move what changed. Songs already on the phone are skipped, so a
second sync of an unchanged library finishes immediately.

## Keeping the phone in step

With **Delete files on the device that are not on the list** ticked, the phone
mirrors your selection exactly. Un-like a song and it disappears from the phone
on the next sync; like a new one and it is sent across. Leave it unticked and
Aeris only ever adds.

## When something is wrong

**The phone is not in Detected.** Open it in your file manager first — that is
what mounts it. If Aeris shows a notice naming your phone, the gvfs bridge is
missing; run the command it gives you.

**Dolphin can see the phone but Aeris cannot.** That is exactly the case the gvfs
bridge fixes. Dolphin is using KDE's own MTP support, which no other program can
reach.

**"…is not available — is the device plugged in and mounted?"** The phone has
unmounted itself, which happens when it locks or the cable is knocked. Unlock
it, reopen it in the file manager, and sync again.

**Transfers are slow.** MTP is slow, especially for many small files. A large
first sync is best left running; later ones only carry the differences.
