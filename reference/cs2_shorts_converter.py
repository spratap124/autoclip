import subprocess
import json
import re
from pathlib import Path
from datetime import datetime
import argparse

# Main gameplay crop (centered)
GAMEPLAY_CROP_W = 608
GAMEPLAY_CROP_H = 1080
GAMEPLAY_CROP_X = 656
GAMEPLAY_CROP_Y = 0

# # Kill feed crop
KILLFEED_CROP_W = 608
KILLFEED_CROP_H = 240
KILLFEED_CROP_X = 1420
KILLFEED_CROP_Y = 50


OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920

# # Height reserved for kill feed bar
TOP_BAR_HEIGHT = 240


# TRIM_LAST_SECONDS = 9
TRIM_LAST_SECONDS = 9


def get_video_metadata(video_file: Path):
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        str(video_file),
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
    )

    return json.loads(result.stdout)


def get_duration(video_file: Path) -> float:
    data = get_video_metadata(video_file)
    return float(data["format"]["duration"])


def safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip()


def get_video_title(video_file: Path) -> str:
    try:
        data = get_video_metadata(video_file)

        tags = data.get("format", {}).get("tags", {})

        for key in (
            "title",
            "TITLE",
            "comment",
            "COMMENT",
            "description",
            "DESCRIPTION",
        ):
            value = tags.get(key)

            if value and value.strip():
                return safe_filename(value)

    except Exception:
        pass

    return safe_filename(video_file.stem)


def convert_video(input_file: Path, output_file: Path):
    duration = get_duration(input_file)

    if duration <= TRIM_LAST_SECONDS + 1:
        print(f"Skipping {input_file.name} (too short)")
        return

    target_duration = duration - TRIM_LAST_SECONDS

    gameplay_height = OUTPUT_HEIGHT - TOP_BAR_HEIGHT

    filter_complex = (
        # Gameplay section
        f"[0:v]"
        f"crop={GAMEPLAY_CROP_W}:{GAMEPLAY_CROP_H}:{GAMEPLAY_CROP_X}:{GAMEPLAY_CROP_Y},"
        f"scale={OUTPUT_WIDTH}:{gameplay_height}:flags=lanczos,"
        f"eq=saturation=1.70:contrast=1.25:brightness=0.04:gamma=1.08"
        f"[gameplay];"

        # Kill feed section
        f"[0:v]"
        f"crop={KILLFEED_CROP_W}:{KILLFEED_CROP_H}:{KILLFEED_CROP_X}:{KILLFEED_CROP_Y},"
        f"scale={OUTPUT_WIDTH}:{TOP_BAR_HEIGHT}:flags=lanczos,"
        f"eq=saturation=1.70:contrast=1.25:brightness=0.04:gamma=1.08"
        f"[killfeed];"


        # Create black canvas
        f"color=size={OUTPUT_WIDTH}x{OUTPUT_HEIGHT}:color=black[base];"

        # Put killfeed at top
        f"[base][killfeed]overlay=0:0[tmp];"

        # Put gameplay below
        f"[tmp][gameplay]overlay=0:{TOP_BAR_HEIGHT}"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_file),

        "-t",
        str(target_duration),

        "-filter_complex",
        filter_complex,

        "-c:v",
        "libx264",
        "-preset",
        "veryslow",
        "-crf",
        "16",
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",

        "-c:a",
        "aac",
        "-b:a",
        "320k",

        str(output_file),
    ]

    print(f"Processing: {input_file.name}")

    subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        check=True,
    )

    print(f"Done: {output_file.name}")


def main():
    parser = argparse.ArgumentParser(
        description="Convert Faceit highlights to YouTube Shorts"
    )

    parser.add_argument(
        "input_folder",
        help="Folder containing highlight videos"
    )

    args = parser.parse_args()

    input_dir = Path(args.input_folder)

    if not input_dir.exists():
        print("Input folder does not exist")
        return

    output_dir = input_dir / datetime.now().strftime("%Y-%m-%d")
    output_dir.mkdir(exist_ok=True)

    extensions = {".mp4", ".mov", ".mkv", ".avi"}

    videos = [
        f for f in input_dir.iterdir()
        if f.is_file() and f.suffix.lower() in extensions
    ]

    if not videos:
        print("No videos found")
        return

    print(f"Found {len(videos)} videos")

    for video in videos:

        title = get_video_title(video)

        output_file = output_dir / f"{title}.mp4"

        counter = 1
        while output_file.exists():
            output_file = output_dir / f"{title}_{counter}.mp4"
            counter += 1

        print(f"Output name: {output_file.name}")

        try:
            convert_video(video, output_file)
        except Exception as e:
            print(f"Failed: {video.name}")
            print(e)

    print("\nFinished")
    print(f"Output folder: {output_dir}")


if __name__ == "__main__":
    main()
