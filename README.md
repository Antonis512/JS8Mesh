# JS8Mesh v0.10.0-beta

JS8Mesh v0.10.0-beta is a human-supervised "mesh" awareness and relay tool for JS8Call.

JS8Mesh is not an automated routing mesh network. In JS8Call, relay stations are
human operators who decide every time whether they want to be part of a relay chain or not.
Human operator is needed to decide if he wants to respond if asked who does he hear.
Human operator is needed to approve each transmition. JS8Mesh helps operators keep track
of who hears whom, compare freshness and SNR, and choose relays. JS8Mesh cannot do more
than JS8Call.

## Documentation

- [USER_MANUAL.md](USER_MANUAL.md)

## Dependencies

For normal Windows users of the packaged `.exe` release:
- no separate `pyjs8call` install is needed

For users running JS8Mesh from source:
- Python is required
- `pyjs8call` is required
- Tk/Tkinter support is required
- the source entry point is `main.py`

Linux users should expect to run JS8Mesh from source unless they create their
own packaged build.

## Run From Source

Windows PowerShell:

```powershell
pip install pyjs8call
python main.py
```

Linux:

```bash
python3 -m pip install pyjs8call
python3 main.py
```

## Compatibility Note

JS8Mesh is designed for JS8Call versions that provide `DIRECTED.TXT`-compatible
directed-message output.

Compatibility should not be assumed with every historical JS8Call version or
every fork/build unless that `DIRECTED.TXT` behavior is present and compatible.

Typical Windows `DIRECTED.TXT` location:
- `%LOCALAPPDATA%\JS8Call\DIRECTED.TXT`
- usually resolves to `C:\Users\<YourUser>\AppData\Local\JS8Call\DIRECTED.TXT`

## License

JS8Mesh is licensed under `GPL-3.0-only`.

This means people may:
- use the software
- study the code
- modify it
- redistribute original or modified versions

If they redistribute modified versions, they must follow the GPL.

See [LICENSE](LICENSE).

## Project Name

Please also read [NAME_USE.md](NAME_USE.md).

Modified versions and forks should use a different name to avoid confusion with
the original JS8Mesh project.

## Credits

- Uses `pyjs8call` for JS8Call integration.
- `"JC"` concepts were inspired in part by JS8Spotter.
