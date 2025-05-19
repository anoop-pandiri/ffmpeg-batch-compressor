# FFmpeg Batch Compression Daemon

## Features

- **Continuous monitoring** of input directory for new video files.
- Compresses videos with customizable video codec, quality (QP), preset, and audio codec.
- Automatically moves processed files to an archive folder for easy cleanup.
- Generates CSV reports, logs and compression statistics.
- Graceful shutdown on interrupt (Ctrl+C).
- Fully configurable via `config.json`.
---

## Requirements

- Python 3.x
- FFmpeg and FFprobe installed and added to your system PATH

---

## Installation

1. Clone the repository

2. Edit the `config.json` to fit your setup and preferences.


## Usage

1. Place media in input_folder

2. execute: python ffmpeg_batch_compressor.py