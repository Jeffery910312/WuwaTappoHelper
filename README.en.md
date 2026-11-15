# WuwaTappo Helper

[繁體中文](ReadMe.md)

A Windows desktop helper for automatic keypresses and chest claiming in WuwaTappo.

## Getting started

1. Install Python 3.11 or later with pip, Tkinter, and the `py` launcher.
2. Run `Setup.bat` once to install dependencies.
3. Open WuwaTappo, then run `Start.bat` and allow administrator access.
4. Enable the features you need and choose a key, rate, and click mode.
5. Click **Start** to run or **Pause** to stop. Close the window to exit.

## Settings

- **Auto keypress**: Keeps sending the selected key, including during chest cooldown.
- **Auto claim**: Claims a ready chest when the click threshold is reached.
- **Foreground / Background**: Foreground mode moves your mouse. Background mode does not move it and never falls back to foreground mode automatically.
- **☀ / ☾ and EN / 中**: Switch the theme and interface language. Settings save automatically.

## Notes

- Automatic keypresses go to the foreground application. Choose an unused key from F13 to F24.
- Keep chests visible and do not minimize the game window. The counter can still be read when another window covers it.
- The main click count tracks input events and is periodically calibrated by OCR. It may show **Reading...** before the first calibration.
- Settings, claim progress, and cooldown are saved in `runtime.json`.

For startup errors, check `startup.log`. For runtime or recognition issues, check `clicker.log`.
