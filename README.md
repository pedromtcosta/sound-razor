# Master Track

## What the application actually is

Master Track is a local app and Python CLI that separates a mixed recording into WAV stems such as guitar, bass, vocals and drums. Its browser UI organizes song projects and plays stems together with mute, solo and volume controls.

FFmpeg converts the input audio, then the `audio-separator` Python library loads and runs a pretrained model. Demucs separates individual instruments; MDX separates vocals from instrumental accompaniment. Our code chooses a model from your arguments and saves the requested outputs.

Python dependencies live in `.venv/`. Model files download automatically on first use and are cached in `.master-track/models/`. Processing a song runs inference—it does not train or modify the model. The outputs are estimates and can contain bleed or artifacts.

## Local setup

Requirements: Python 3.11, FFmpeg on `PATH`, and uv. From the project directory:

```sh
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e '.[separation]'
```

If this environment is already installed, skip setup. Commands below use its executable directly; activation is unnecessary.

## Open the UI

```sh
.venv/bin/master-track ui
```

Open `http://127.0.0.1:8765` in your browser. Choose a projects folder with the built-in folder browser, or supply it at startup:

```sh
.venv/bin/master-track ui --projects ./projects
```

The folder selection is remembered in `.master-track/ui.json`. Import a recording, optionally enter its title, artist, album and year, then choose **Vocals & instrumental** or **Custom** (select individual stems) and click **Separate tracks**. The original is copied into a new song folder; duplicate imports get separate folders. Use **Home** (or the logo) to return to the home screen. **Delete song** asks for confirmation before permanently removing that project folder, including its recording, metadata and stems.

```text
projects/
└── Queensryche - Jet City Woman/
    ├── Queensryche_Jet_City_Woman.opus
    ├── metadata.json
    └── stems/
        ├── guitar.wav
        ├── bass.wav
        ├── vocals.wav
        └── drums.wav
```

Existing project folders are discovered automatically. Metadata is optional: one audio file at the project root is treated as the original, and WAVs in `stems/` are playable. If multiple originals exist, set `original_file` in `metadata.json`. Main metadata fields are `title`, `artist`, `album` and `year`; they can also be edited in the UI. The CLI's older adjacent-output layout is not automatically moved into this project layout.

The UI runs the existing CLI in a background subprocess, with one separation at a time, live process logs and cancellation. Successful reruns replace the project's `stems/` directory; failed or cancelled runs preserve previous stems. Job status is held for the running server session.

The player schedules all stems on one audio clock. It provides play/pause, seek, mute, solo, per-stem volume and master volume; the space bar toggles playback outside input controls. All unmuted stems form the reconstructed mix. Select **Other** to include every unselected part: for example, Guitar + Bass + Vocals + Drums + Other folds piano into Other. Without Other, unselected parts are omitted. This applies to the UI and CLI; rerun separation to update existing stems. Stems load into browser memory for synchronized playback, so long recordings or many tracks can use substantial RAM. Only the current project's audio is retained by the player.

The server listens only on your computer's loopback address. No web framework or frontend build is required. Use `--port` to change the port and `--cache` to change the model cache. Stop the server with Ctrl+C; this also cancels an active separation. Custom separation offers vocals, guitar, bass, drums, piano and other. Lead/rhythm remains available only through the optional CLI stage.

## Separate a recording with the CLI

```sh
.venv/bin/master-track separate \
  --file ./files/Queensryche_Jet_City_Woman.opus \
  --stems guitar bass vocals drums
```

The output folder is created automatically next to the original file:

```text
files/Queensryche_Jet_City_Woman/
├── guitar.wav
├── bass.wav
├── vocals.wav
└── drums.wav
```

Use `--output` (or `--output-folder`) to choose an exact destination:

```sh
.venv/bin/master-track separate --file ./files/song.opus \
  --stems guitar bass vocals drums --output ./my-output
```

Reruns replace matching stem files and leave unrelated files intact. The command prints a JSON array of absolute output paths; progress and errors go to stderr.

| Arguments | Behavior |
| --- | --- |
| No `--stems` | Save all six Demucs stems: vocals, drums, bass, guitar, piano and other |
| `--stems guitar bass vocals drums` | Save those four instrument stems |
| `--stems vocals instrumental` | Use the two-stem MDX model |
| `--stems vocals` | Save vocals only |
| `--model htdemucs.yaml` | Explicitly select four-stem Demucs: vocals, drums, bass and other |
| `--cache ./model-cache` | Override the model download directory |

Model selection is automatic unless overridden. Selecting fewer files does not necessarily reduce inference work. `guitar` combines rhythm and lead.

```sh
.venv/bin/master-track separate --help
```

## Optional inputs

### YouTube

Install the YouTube extra and a supported JavaScript runtime such as Deno:

```sh
uv pip install --python .venv/bin/python -e '.[separation,youtube]'
.venv/bin/master-track separate \
  --youtube 'https://www.youtube.com/watch?v=VIDEO_ID' \
  --stems guitar bass vocals drums --output ./my-output
```

This downloads audio before processing; it is not live streaming. Use audio you are authorized to download and process. See [yt-dlp's runtime setup](https://github.com/yt-dlp/yt-dlp/wiki/EJS). Without `--output`, YouTube results go into a unique folder under `stems/`.

### Spotify metadata

Set `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` in your environment, then run:

```sh
.venv/bin/master-track spotify 'spotify:track:TRACK_ID'
```

This returns track metadata only. It does not download Spotify audio or automatically find a YouTube recording.

## Optional lead/rhythm separation

Requesting `lead` or `rhythm` adds a second stage: SAM Audio processes the Demucs guitar stem. `lead.wav` is the target estimate; `rhythm.wav` is the remaining-guitar estimate. Both are mono.

This stage is implemented but has not been verified end-to-end here. It requires the substantial optional SAM dependency stack, Git, and access to the [SAM Audio checkpoint](https://huggingface.co/facebook/sam-audio-small):

```sh
uv pip install --python .venv/bin/python -e '.[separation,guitars]'
.venv/bin/hf auth login
.venv/bin/master-track separate --file ./files/song.opus \
  --stems lead rhythm bass vocals drums
```

Authenticate after checkpoint access is granted. Optional `--lead-prompt` and `--lead-span START END` guide extraction. The stage uses CUDA when available, otherwise CPU; CPU processing can be slow. Ordinary instrument separation does not require SAM.

Local MDX and Demucs inference have been verified. YouTube acquisition and Spotify authentication have not been verified in this session. The `train` command is explicitly unimplemented.
