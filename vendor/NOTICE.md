# Source provenance and notices

The Python/QML sources in `ovb_rc003` and `remote_scan_bootstrap.py` are copied
without modification from:

- https://github.com/ZSTDJan/windows-remote-mic-app
- commit `07fae8034f0210cca90f8b913491044555bd9f6d`
- original subtree `apps/windows/rc003/src/`

Its Windows implementation is based on the GPL-3.0-only project
https://github.com/nijez/open-voice-bridge . The exact upstream attribution and
copyright documents are retained in `UPSTREAM-ATTRIBUTION.md` and
`UPSTREAM-COPYRIGHT.md`; relative paths in those historical documents refer to
the original upstream repository. `UPSTREAM-LICENSE.md` is the unmodified
upstream root GPLv3 text. Copyright remains with the respective contributors.

This adapter reuses exact-build-verified Doubao shortcut/TSF integration and
Windows device attribution utilities. It does not run the upstream GUI. The
upstream application's three branded SVG/ICO files are omitted and no trademark
or icon license is granted. Other unused upstream source is retained to avoid
an incomplete dynamic import dependency closure; it is not a supported GUI.

`src/rm9451`, the entry points, configuration/setup helpers, tests and this
integration are provided under GPL-3.0-only; see the repository LICENSE.
Modifications include the clone-specific protocol and hold-cycle logic,
fragment recovery, key mapping, bounded supervision and public local settings.

This source release does not bundle Windows/IME binaries, VB-CABLE, Python,
Frida, NumPy, sounddevice/PortAudio, or their installers. Runtime dependencies
are installed separately under their own licenses. Any future binary release
must include the corresponding license notices and complete corresponding
source/build instructions for the components actually distributed.
