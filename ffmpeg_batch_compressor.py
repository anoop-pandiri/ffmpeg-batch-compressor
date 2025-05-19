import subprocess
import time
import os
import sys
import shutil
import signal
import logging
import re
import csv
from datetime import datetime
import itertools
import json
REQUIRED_KEYS = {
    "qp_value",
    "video_codec",
    "audio_codec",
    "preset_val",
    "op_extension",
    "input_folder",
    "output_folder",
    "archive_folder",
    "log_file",
    "csv_filename_pattern",
    "video_extensions",
    "retry_wait_seconds"
}

def load_config(path='config.json'):
    with open(path) as f:
        cfg = json.load(f)
    missing = REQUIRED_KEYS - cfg.keys()
    if missing:
        raise KeyError(f"Missing keys: {missing}")
    return cfg

config = load_config()

qp_value = config["qp_value"]
video_codec = config["video_codec"]
audio_codec = config["audio_codec"]
preset_val = config["preset_val"]
op_extension = config["op_extension"]
input_folder = config["input_folder"]
output_folder = config["output_folder"]
archive_folder = config["archive_folder"]
log_file = config["log_file"]
csv_filename_pattern = config["csv_filename_pattern"]
csv_filename = time.strftime(csv_filename_pattern)
video_extensions = tuple(config["video_extensions"])
retry_wait_seconds = config["retry_wait_seconds"]

total_input_size = 0
total_output_size = 0
file_count = 0
total_files_temp = 0
running = True
aborted = False

def sanitize_filename(name):
    return re.sub(r'[^a-zA-Z0-9\-_.]', '_', name)

os.makedirs(input_folder, exist_ok=True)
os.makedirs(output_folder, exist_ok=True)
os.makedirs(archive_folder, exist_ok=True)

def get_video_duration(input_file):
    cmd = [
        'ffprobe',
        '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'json',
        input_file
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    info = json.loads(result.stdout)
    duration = float(info['format']['duration'])
    return duration

logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logging.info(f"Started with settings: QP={qp_value}, Video codec={video_codec}, Audio codec={audio_codec}, Preset={preset_val}")

def handle_exit(signum, frame):
    global running, aborted
    print("\nInterrupt received. Stopping ...")
    logging.info("Exit signal received. Aborting current file and shutting down...")
    running = False
    aborted = True

signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)

def get_file_size_mb(filepath):
    if os.path.exists(filepath):
        return os.path.getsize(filepath) / (1024 * 1024)
    return 0

def waiting_animation():
    frames = ['   ', '.  ', '.. ', '...']
    for frame in itertools.cycle(frames):
        yield f"\rWaiting for files{frame} "

def seconds_to_hms(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def format_time(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def print_progress_bar(percent, elapsed, eta, bar_length=50):
    filled_len = int(bar_length * percent // 100)
    bar = "█" * filled_len + '-' * (bar_length - filled_len)
    print(f"\rProgress: |{bar}| {percent:6.2f}% | Elapsed: {format_time(elapsed)} | ETA: {format_time(eta)}", end='', flush=True)


def run_ffmpeg_with_progress(cmd, total_duration):
    global aborted
    cmd = cmd + ['-progress', 'pipe:1', '-nostats']
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)

    start_time = time.time()
    current_time = 0

    while True:
        line = process.stdout.readline()
        if not line:
            break

        line = line.strip()
        if line.startswith("out_time_ms="):
            value = line.split('=')[1]
            if value.isdigit():
                out_time_ms = int(value)
                current_time = out_time_ms / 1_000_000
                percent = (current_time / total_duration) * 100

                if percent > 99.5:
                    percent = 100.0

                elapsed = time.time() - start_time
                eta = (elapsed / (percent / 100)) - elapsed if percent > 0 else 0
                print_progress_bar(percent, elapsed, eta)

        if aborted:
            print("\nAborted! Killing ffmpeg process...")
            if process.poll() is None:
                process.kill()
                process.wait()
            return False

    retcode = process.wait()
    if retcode != 0:
        print(f"\nFFmpeg failed with exit code {retcode}")
        return False
    else:
        elapsed = time.time() - start_time
        print_progress_bar(100, elapsed, 0)
        print("\n")
        return True



def print_summary_box(file_count, total_input, total_output):
    space_saved = total_input - total_output
    saved_percent = (space_saved / total_input) * 100 if total_input > 0 else 0

    line_len = 48

    def pad_line(label, value_str):
        inner_width = line_len - 4 
        total_len = len(label) + len(value_str)
        padding = inner_width - total_len
        return f"║ {label}{value_str}{' ' * padding} ║"


    print("╔" + "═" * (line_len - 2) + "╗")
    print(f"║{'Compression Summary':^{line_len - 2}}║")
    print("╠" + "═" * (line_len - 2) + "╣")
    print(pad_line("Files processed : ", f"{file_count}"))
    print(pad_line("Total input     : ", f"{total_input:8.2f} MB"))
    print(pad_line("Total output    : ", f"{total_output:8.2f} MB"))
    print(pad_line("Space saved     : ", f"{space_saved:8.2f} MB ({saved_percent:5.2f}%)"))
    print("╚" + "═" * (line_len - 2) + "╝")




with open(csv_filename, 'w', newline='') as csvfile:
    csvwriter = csv.writer(csvfile)
    csvwriter.writerow(['Input File', 'Output File', 'Input Size (MB)', 'Output Size (MB)', 'Compression %', 'Duration (s)', 'Start Time', 'End Time'])

    while running:
        video_files = [f for f in os.listdir(input_folder) if f.lower().endswith(video_extensions)]

        if video_files:
            total_files_temp += len(video_files)
            for input_file in video_files:
                aborted = False
                if not running:
                    break

                input_path = os.path.join(input_folder, input_file)
                base_name = sanitize_filename(os.path.splitext(input_file)[0])
                output_filename = f"{base_name}_[ffmpeg_{video_codec}_{audio_codec}_{preset_val}_QP{qp_value}].{op_extension}"
                output_path = os.path.join(output_folder, output_filename)

                start_time = time.time()
                start_str = time.strftime('%H:%M:%S', time.localtime(start_time))
                logging.info(f"Processing {input_file}")

                total_duration = get_video_duration(input_path)

                cmd = [
                    'ffmpeg',
                    '-hide_banner',
                    '-loglevel', 'error',
                    '-i', input_path,
                    '-c:v', video_codec,
                    '-rc', 'constqp',
                    '-qp', str(qp_value),
                    '-preset', preset_val,
                    '-c:a', audio_codec,
                    output_path,
                    '-y'
                ]

                try:
                    print(f"\rFile {file_count + 1}/{total_files_temp}: {input_file}")
                    success = run_ffmpeg_with_progress(cmd, total_duration)
                    if not success:
                        print("Cleaning up incomplete output file...")
                        if os.path.exists(output_path):
                            os.remove(output_path)
                        break
                except subprocess.CalledProcessError as e:
                    logging.error(f"Compression failed for {input_file}: {e}")
                    continue
                file_count += 1
                end_time = time.time()
                end_str = time.strftime('%H:%M:%S', time.localtime(end_time))
                duration = end_time - start_time

                input_size = get_file_size_mb(input_path)
                output_size = get_file_size_mb(output_path)
                compression_percent = (output_size / input_size) * 100 if input_size > 0 else 0
                total_input_size += input_size
                total_output_size += output_size

                csvwriter.writerow([input_file, output_filename, f"{input_size:.2f}", f"{output_size:.2f}", f"{compression_percent:.2f}", f"{duration:.2f}", start_str, end_str])
                csvfile.flush()

                try:
                    shutil.move(input_path, os.path.join(archive_folder, input_file))
                    logging.info(f"Moved {input_file} to archive")
                except Exception as e:
                    logging.warning(f"Failed to archive {input_file}: {e}")
        else:
            logging.info(f"No files found. Retrying in {retry_wait_seconds}s...")
            wait_anim = waiting_animation()
            for _ in range(retry_wait_seconds):
                if not running:
                    break
                print(next(wait_anim), end='', flush=True)
                time.sleep(1)
            print('\r' + ' ' * 40 + '\r', end='', flush=True)

space_saved = total_input_size - total_output_size
saved_space_percent = (space_saved / total_input_size) * 100 if total_input_size > 0 else 0

summary = (
    f"Summary: Files={file_count}, Input={total_input_size:.2f} MB, "
    f"Output={total_output_size:.2f} MB, Saved={space_saved:.2f} MB ({saved_space_percent:.2f}%)"
)
logging.info(summary)
print_summary_box(file_count, total_input_size, total_output_size)
print(f"CSV: {csv_filename}")
print(f"Log: {log_file}")
