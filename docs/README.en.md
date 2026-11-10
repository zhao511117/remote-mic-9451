# RemoteMic 9451

This source release connects one tested **18D1:9451:0105 / ZTKA-IR57 Google TV-style Bluetooth voice remote** to Windows dictation. It is not a universal Android TV remote driver and has not been ported to macOS.

It receives the remote's ATVV audio, decodes its 134-byte IMA ADPCM frames, recovers damaged notification fragments, and sends 48 kHz PCM through VB-CABLE to Doubao Input Method. The microphone button controls both remote audio capture and a held right-Alt shortcut. A clone-specific ten-second close/reopen cycle works around the sample's approximately fifteen-second stream limit without logically releasing the shortcut. A roughly 31-second utterance has been tested; the full 120-second protection limit has not.

## Setup

1. Install 64-bit Python 3.13, official VB-CABLE and Doubao Input Method. The tested IME build is 1.0.0.23; hooks reject unsupported file hashes/signatures.
2. Pair the remote in Windows Bluetooth settings.
3. Run `setup.ps1` in the source directory. It creates `.venv`, installs pinned dependencies, and selects an already-paired 9451. Multiple candidates require `configure.py --device N`.
4. Keep `config.local.json` private. Configure Doubao to use **CABLE Output** and held **right Alt**. The receiver writes to **CABLE Input** using WASAPI. Your default physical microphone can remain unchanged.
5. Remove conflicting Alt-based voice shortcuts in the target application. Run `.venv/Scripts/python.exe doctor.py`, then `start.cmd`. Focus a text field yourself, wake the remote and hold its voice button to speak.
6. Use `stop.cmd` to stop the receiver, key mapper and supervisor together. `login-startup.ps1` installs a current-user Startup shortcut; `login-startup.ps1 -Remove` removes only that shortcut.

## Optional extra buttons

Extra native-event suppression is **experimental and disabled by default**. Enable using `configure.py --enable-extra-keys`, then restart the product. It only supports the pinned Windows HID driver hash documented in the [main README](../README.md). It attaches Frida to the selected HID host, validates the adapter identity and callback bytes, and neutralizes only known extra-button reports while a two-second lease is valid. Unknown builds fail without reusing old offsets. UAC/Frida Helper prompts may occur; this project does not disable those controls.

Directions/OK become cursor keys/Enter; Back=Esc, Home=Win+D, mute stays native, YouTube toggles WorkBuddy foreground/minimized, Netflix toggles the Codex desktop app, Source opens Win+Tab, and Power performs no action while suppression is active. No volume-button report was observed on the tested sample. Application paths can be configured locally.

The supervisor can recover a separately exited component after a stable run, with at most two automatic recoveries per component per hour. Failed starts do not form an attachment loop. It cannot recover if the supervisor itself is terminated, nor does process existence establish audio health.

## Development and license

Set `PYTHONPATH` to `src`, run `python -B -m unittest discover -s tests -v`, and optionally run `node tests/key-policy.cjs src/rm9451/key_mapping.js`. Hardware/desktop integration checks are opt-in. CI cannot prove physical compatibility.

GPL-3.0-only. The bundled unmodified `ovb_rc003` source comes from `ZSTDJan/windows-remote-mic-app@07fae8034f0210cca90f8b913491044555bd9f6d`, itself based on `nijez/open-voice-bridge`. See [NOTICE](../vendor/NOTICE.md). No proprietary IME, Windows driver, virtual audio driver, third-party binary, personal device identity or runtime recording is distributed.
